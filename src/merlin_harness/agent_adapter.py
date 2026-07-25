"""Agent-independent contract for evidence-bearing task execution.

The adapter boundary deliberately separates a model's selected skill from a
provider-observed skill-body load or invocation.  The latter is the only input
that may feed paper-grade invocation metrics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models import AgentRunContract, AgentRunResult, SkillArtifact, TaskSpec


class AgentContractError(ValueError):
    """Raised when an adapter run cannot be trusted as a comparable trace."""


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(slots=True)
class AgentRunRequest:
    """The only task execution input an adapter receives from Merlin."""

    contract: AgentRunContract
    task: TaskSpec
    workspace: Path
    provisioned_skills: list[SkillArtifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgentAdapter(Protocol):
    """Execute one already-materialized task and return normalized evidence."""

    name: str

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run the base agent without invoking the task verifier."""


def _resolved(path_text: str | Path) -> Path:
    return Path(path_text).expanduser().resolve()


def _require_nonempty(value: str | None, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AgentContractError(f"agent run contract requires non-empty {field_name}")


def validate_agent_run_contract(contract: AgentRunContract) -> None:
    """Reject incomplete, non-portable, or ambiguous execution conditions."""

    if contract.schema_version != 1:
        raise AgentContractError(f"unsupported agent run contract schema_version: {contract.schema_version}")
    for field_name in (
        "run_id",
        "task_id",
        "condition",
        "workspace_root",
        "raw_trace_root",
        "agent_id",
        "agent_version",
        "backend",
        "model_id",
        "budget_id",
        "library_snapshot_id",
        "library_snapshot_sha256",
        "verifier_id",
    ):
        _require_nonempty(getattr(contract, field_name), field_name=field_name)
    if contract.effort is not None and not contract.effort.strip():
        raise AgentContractError("agent run contract effort must be non-empty when provided")
    if not Path(contract.workspace_root).is_absolute():
        raise AgentContractError("agent run contract workspace_root must be absolute")
    if not Path(contract.raw_trace_root).is_absolute():
        raise AgentContractError("agent run contract raw_trace_root must be absolute")
    if not _SHA256_RE.fullmatch(contract.library_snapshot_sha256):
        raise AgentContractError("agent run contract library_snapshot_sha256 must be a lowercase SHA-256")


def validate_agent_run_request(request: AgentRunRequest) -> None:
    """Validate the request before an adapter receives task access."""

    validate_agent_run_contract(request.contract)
    if request.task.id != request.contract.task_id:
        raise AgentContractError(
            f"task/run contract mismatch: request task_id={request.task.id!r} "
            f"contract task_id={request.contract.task_id!r}"
        )
    requested_workspace = _resolved(request.workspace)
    contract_workspace = _resolved(request.contract.workspace_root)
    if requested_workspace != contract_workspace:
        raise AgentContractError(
            "requested workspace escapes or differs from contract workspace_root: "
            f"{requested_workspace} != {contract_workspace}"
        )
    skill_ids = [skill.id for skill in request.provisioned_skills]
    if len(set(skill_ids)) != len(skill_ids):
        raise AgentContractError("provisioned skill ids must be unique")


def validate_agent_run_result(request: AgentRunRequest, result: AgentRunResult) -> None:
    """Validate adapter output before the verifier is allowed to run."""

    validate_agent_run_request(request)
    if not isinstance(result, AgentRunResult):
        raise AgentContractError("adapter must return an AgentRunResult")
    if result.contract != request.contract:
        raise AgentContractError("adapter result contract does not exactly match the requested contract")
    if not isinstance(result.actual_invocation_evidence_complete, bool):
        raise AgentContractError("actual_invocation_evidence_complete must be boolean")
    if not isinstance(result.selected_skill_ids, list):
        raise AgentContractError("adapter result selected_skill_ids must be a list")
    if not isinstance(result.invocation_events, list):
        raise AgentContractError("adapter result invocation_events must be a list")
    result_workspace = _resolved(result.workspace_root)
    request_workspace = _resolved(request.workspace)
    if result_workspace != request_workspace:
        raise AgentContractError(
            "adapter result workspace_root escapes or differs from requested workspace: "
            f"{result_workspace} != {request_workspace}"
        )

    provisioned_skill_ids = {skill.id for skill in request.provisioned_skills}
    selected_skill_ids = list(result.selected_skill_ids)
    if len(set(selected_skill_ids)) != len(selected_skill_ids):
        raise AgentContractError("adapter result selected_skill_ids must be unique")
    unknown_selected = sorted(set(selected_skill_ids) - provisioned_skill_ids)
    if unknown_selected:
        raise AgentContractError(
            f"adapter result selected skill was not provisioned: {', '.join(unknown_selected)}"
        )

    event_ids: set[str] = set()
    previous_sequence = -1
    unknown_invoked: set[str] = set()
    for event in result.invocation_events:
        if not event.skill_id.strip() or not event.source.strip() or not event.event_id.strip():
            raise AgentContractError("skill invocation events require skill_id, source, and event_id")
        if event.event_kind not in {"skill_body_loaded", "provider_skill_invocation"}:
            raise AgentContractError(f"unsupported actual skill invocation event_kind: {event.event_kind}")
        if event.event_id in event_ids:
            raise AgentContractError(f"duplicate skill invocation event_id: {event.event_id}")
        if event.sequence < 0 or event.sequence <= previous_sequence:
            raise AgentContractError("skill invocation event sequences must be strictly increasing")
        event_ids.add(event.event_id)
        previous_sequence = event.sequence
        if event.skill_id not in provisioned_skill_ids:
            unknown_invoked.add(event.skill_id)
    if unknown_invoked:
        raise AgentContractError(
            "adapter result invoked skills outside the provisioned set: "
            + ", ".join(sorted(unknown_invoked))
        )

    # Import here so agent_adapter remains a low-level contract module and does
    # not create an import cycle with traces' serialization helpers.
    from .traces import verify_raw_trace_reference

    verify_raw_trace_reference(result.raw_trace, raw_trace_root=request.contract.raw_trace_root)
