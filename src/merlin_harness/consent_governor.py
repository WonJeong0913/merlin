"""Risk-tiered bounded autonomy for Merlin's skill harness.

Managed mode auto-authorizes only a frozen low-risk, reversible, session-scoped
envelope. Strict mode requires explicit natural-language permission for every
skill write. The first production slice intentionally supports one registered
operation whose verifier contract already exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .governed_provisioning import GovernedProvisioner, active_library_snapshot
from .library import FileSkillLibrary
from .managed_creation import (
    CreationCase,
    ManagedCreationError,
    ManagedSkillDraft,
    ManagedSkillProposal,
    run_managed_creation,
)
from .models import LifecycleStatus, SkillArtifact


ConsentDecision = Literal["approved", "declined", "ambiguous"]
GovernorStatus = Literal["adopted", "declined", "ambiguous", "blocked"]
ApprovalMode = Literal["managed", "strict"]

MAX_AUTONOMY_REQUEST_CHARS = 2_000
POLICY_VERSION = "risk-tiered-bounded-autonomy-v2"
TODO_CAPABILITY_ID = "todo-file-extraction-v1"
TODO_SKILL_ID = "extract-todo-items"

_APPROVE_PHRASES = frozenset(
    {
        "yes",
        "yes proceed",
        "approve",
        "approved",
        "allow",
        "proceed",
        "네",
        "예",
        "응",
        "좋아",
        "허락",
        "허락해",
        "승인",
        "승인해",
        "진행",
        "진행해",
        "진행해줘",
        "해줘",
        "네 진행해",
        "네 진행해줘",
    }
)
_DECLINE_PHRASES = frozenset(
    {
        "no",
        "cancel",
        "deny",
        "decline",
        "stop",
        "아니",
        "아니요",
        "취소",
        "거절",
        "중지",
        "하지마",
        "하지 마",
        "안돼",
        "안 돼",
    }
)
_NEGATIVE_REQUEST_PHRASES = (
    "do not",
    "don't",
    "dont",
    "without creating",
    "하지 마",
    "하지마",
    "만들지 마",
    "만들지마",
    "금지",
)
_POSITIVE_INTENTS = (
    "extract",
    "collect",
    "convert",
    "write",
    "create",
    "추출",
    "변환",
    "저장",
    "모아",
    "만들",
)


class ConsentGovernorError(RuntimeError):
    """Fail-closed bounded-autonomy error safe to show to a user."""


@dataclass(frozen=True, slots=True)
class AutonomyProposal:
    schema_version: int
    policy_version: str
    proposal_id: str
    capability_id: str
    candidate_skill_id: str
    request_sha256: str
    request_chars: int
    request_stored: bool
    source_library_snapshot_sha256: str
    action: str
    risk_class: str
    approval_mode: str
    permission_required: bool
    permission_reason: str
    planned_mutations: tuple[str, ...]
    provider_calls_for_skill_change: int
    ordinary_chat_resume_is_separate: bool
    verifier_contract: tuple[str, ...]
    rollback_policy: str

    def to_safe_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AutonomyAdoption:
    status: GovernorStatus
    proposal: AutonomyProposal
    library: FileSkillLibrary | None = None
    skill_bundle_paths: dict[str, Path] | None = None
    creation_evidence: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(slots=True)
class _PendingProposal:
    public: AutonomyProposal
    original_request: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected(*items: str) -> str:
    return json.dumps(
        {"items": list(items)}, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"


def _normalize_reply(value: str) -> str:
    normalized = " ".join(value.strip().lower().split())
    normalized = re.sub(r"[,，]", "", normalized)
    return re.sub(r"[.!?。！？]+$", "", normalized).strip()


def classify_consent(value: str) -> ConsentDecision:
    """Accept only an exact allow/deny utterance; never infer from substrings."""

    normalized = _normalize_reply(value)
    if normalized in _APPROVE_PHRASES:
        return "approved"
    if normalized in _DECLINE_PHRASES:
        return "declined"
    return "ambiguous"


def managed_auto_authorization_eligible(
    proposal: AutonomyProposal, *, approval_mode: ApprovalMode
) -> bool:
    """Return true only for the complete frozen Managed v2 safety envelope."""

    return (
        approval_mode == "managed"
        and not proposal.permission_required
        and proposal.risk_class == "low_reversible_registered_operation"
        and proposal.action == "compile_verify_and_stage_registered_skill"
        and proposal.provider_calls_for_skill_change == 0
        and proposal.rollback_policy
        == "fail closed; keep the source library unchanged"
        and proposal.planned_mutations
        == (
            "new-only verification evidence inside this chat workspace",
            "copy-on-write session library overlay after all gates pass",
        )
    )


def _todo_contract(
    *,
    proposal: AutonomyProposal,
    existing_skills: tuple[SkillArtifact, ...],
) -> tuple[ManagedSkillProposal, ManagedSkillDraft]:
    cases = (
        CreationCase(
            id="target-english",
            prompt="From backlog.todo, extract TODO-prefixed entries into todo-items.json.",
            split="target",
            should_trigger=True,
            input_files=(("backlog.todo", "TODO: fix login\nnote: investigate\nTODO: write tests\n"),),
            expected_files=(("todo-items.json", _expected("fix login", "write tests")),),
        ),
        CreationCase(
            id="target-whitespace",
            prompt="Collect TODO lines in backlog.todo into todo-items.json.",
            split="target",
            should_trigger=True,
            input_files=(("backlog.todo", "  TODO: ship release\nDONE: old item\nTODO: update docs\n"),),
            expected_files=(("todo-items.json", _expected("ship release", "update docs")),),
        ),
        CreationCase(
            id="held-out-korean",
            prompt="backlog.todo에서 TODO 항목을 추출해 todo-items.json으로 저장해줘.",
            split="held_out",
            should_trigger=True,
            input_files=(("backlog.todo", "TODO: 회귀 테스트\n메모: 확인\nTODO: 문서 갱신\n"),),
            expected_files=(("todo-items.json", _expected("회귀 테스트", "문서 갱신")),),
        ),
        CreationCase(
            id="negative-line-summary",
            prompt="Count non-empty lines in input.txt and write summary.txt.",
            split="negative",
            should_trigger=False,
        ),
        CreationCase(
            id="negative-file-artifact",
            prompt="Create report.md in the workspace.",
            split="negative",
            should_trigger=False,
        ),
    )
    generator_prompt = (
        "Compile the registered TODO extraction operation only after consent, "
        "then run the frozen target, held-out, negative, and adoption gates."
    )
    managed_proposal = ManagedSkillProposal(
        proposal_id=proposal.proposal_id,
        candidate_skill_id=proposal.candidate_skill_id,
        source_type="capability_gap",
        provenance_trace_ids=(f"request-{proposal.request_sha256[:24]}",),
        cases=cases,
        frozen_library_snapshot_sha256=active_library_snapshot(existing_skills)[1],
        generator_backend="merlin-registered-operation-compiler",
        generator_model="frozen-operation-registry-v1",
        generator_effort="deterministic",
        generator_prompt_sha256=_sha256_text(generator_prompt),
    )
    draft = ManagedSkillDraft(
        skill_id=TODO_SKILL_ID,
        display_name="Extract TODO Items",
        description=(
            "Extract TODO-prefixed action items from backlog.todo into todo-items.json. "
            "Use only for this explicit input/output contract."
        ),
        trigger="Use when backlog.todo must become todo-items.json from TODO-prefixed lines.",
        do_not_use_when=(
            "Do not use to count non-empty lines in input.txt or write summary.txt.",
            "Do not use to create report.md or arbitrary files without TODO extraction.",
        ),
        operation_id="extract-prefixed-lines-to-json",
        input_path="backlog.todo",
        output_path="todo-items.json",
        prefix="TODO:",
        default_prompt="Use $extract-todo-items to turn backlog.todo TODO lines into todo-items.json.",
    )
    return managed_proposal, draft


class ConsentGatedHarnessGovernor:
    """Detect, propose, and verify changes while reserving authority to the user."""

    def __init__(
        self,
        *,
        trace_root: str | Path,
        approval_mode: ApprovalMode = "managed",
        max_actions_per_session: int = 1,
    ) -> None:
        resolved = Path(trace_root).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError("trace_root must be an existing directory")
        if isinstance(max_actions_per_session, bool) or not 1 <= max_actions_per_session <= 10:
            raise ValueError("max_actions_per_session must be from 1 through 10")
        if approval_mode not in {"managed", "strict"}:
            raise ValueError("approval_mode must be managed or strict")
        self.trace_root = resolved
        self.approval_mode = approval_mode
        self.max_actions_per_session = max_actions_per_session
        self.completed_actions = 0
        self._pending: _PendingProposal | None = None
        self._seen_request_hashes: set[str] = set()

    @property
    def pending(self) -> AutonomyProposal | None:
        return self._pending.public if self._pending is not None else None

    @property
    def pending_original_request(self) -> str | None:
        return self._pending.original_request if self._pending is not None else None

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy_version": POLICY_VERSION,
            "mode": self.approval_mode,
            "authority_policy": "risk_tiered_selective_approval",
            "completed_actions": self.completed_actions,
            "max_actions_per_session": self.max_actions_per_session,
            "pending": self.pending.to_safe_dict() if self.pending else None,
        }

    def consider(
        self, request: str, library: FileSkillLibrary
    ) -> AutonomyProposal | None:
        """Perform read-only need detection; this method never writes or calls a model."""

        if self._pending is not None or self.completed_actions >= self.max_actions_per_session:
            return None
        if (
            not request.strip()
            or "\x00" in request
            or len(request) > MAX_AUTONOMY_REQUEST_CHARS
        ):
            return None
        lowered = request.lower()
        if any(phrase in lowered for phrase in _NEGATIVE_REQUEST_PHRASES):
            return None
        if not all(anchor in lowered for anchor in ("todo", "backlog.todo", "todo-items.json")):
            return None
        if not any(intent in lowered for intent in _POSITIVE_INTENTS):
            return None
        request_hash = _sha256_text(request)
        if request_hash in self._seen_request_hashes:
            return None
        existing = tuple(library.list())
        if any(
            skill.id == TODO_SKILL_ID and skill.status == LifecycleStatus.ACTIVE
            for skill in existing
        ):
            return None
        decision = GovernedProvisioner(exposure_budget=1).decide(request, existing)
        if decision.primary_id is not None and decision.candidate(
            decision.primary_id
        ).exact_anchor_evidence:
            return None
        snapshot = active_library_snapshot(existing)[1]
        sequence = self.completed_actions + 1
        permission_required = self.approval_mode == "strict"
        public = AutonomyProposal(
            schema_version=2,
            policy_version=POLICY_VERSION,
            proposal_id=f"autonomy-{sequence:03d}-todo-{request_hash[:12]}",
            capability_id=TODO_CAPABILITY_ID,
            candidate_skill_id=TODO_SKILL_ID,
            request_sha256=request_hash,
            request_chars=len(request),
            request_stored=False,
            source_library_snapshot_sha256=snapshot,
            action="compile_verify_and_stage_registered_skill",
            risk_class="low_reversible_registered_operation",
            approval_mode=self.approval_mode,
            permission_required=permission_required,
            permission_reason=(
                "strict mode requires approval for every skill write"
                if permission_required
                else "managed policy pre-authorizes low-risk reversible session changes"
            ),
            planned_mutations=(
                "new-only verification evidence inside this chat workspace",
                "copy-on-write session library overlay after all gates pass",
            ),
            provider_calls_for_skill_change=0,
            ordinary_chat_resume_is_separate=True,
            verifier_contract=(
                "G0 capability gap and snapshot",
                "G1 portable format",
                "G2 static safety",
                "G3 positive and negative routing",
                "G4 same-verifier target gain",
                "G5 held-out regression safety",
                "G6 copy-on-write adoption",
            ),
            rollback_policy="fail closed; keep the source library unchanged",
        )
        self._pending = _PendingProposal(public=public, original_request=request)
        self._seen_request_hashes.add(request_hash)
        return public

    def render_permission_request(self) -> str:
        if self._pending is None:
            raise ConsentGovernorError("no autonomy proposal is pending")
        proposal = self._pending.public
        if not proposal.permission_required:
            raise ConsentGovernorError(
                "managed low-risk proposal is policy-authorized, not permission-pending"
            )
        return (
            "capability gap detected: backlog.todo → todo-items.json. "
            "May I compile and verify the registered extract-todo-items skill, "
            "then activate only the copy-on-write session overlay if G0–G6 all pass? "
            "The skill change itself makes no provider authoring call and never modifies "
            "the source library; the approved original chat request resumes separately. "
            "Reply yes/no (네/아니요)."
        )

    def decline(self) -> AutonomyAdoption:
        if self._pending is None:
            raise ConsentGovernorError("no autonomy proposal is pending")
        proposal = self._pending.public
        self._pending = None
        return AutonomyAdoption(
            status="declined",
            proposal=proposal,
            reason="user declined the authority boundary",
        )

    def resolve_permission(
        self, reply: str, library: FileSkillLibrary
    ) -> AutonomyAdoption:
        """Resolve an exact natural-language decision inside the core boundary."""

        if self._pending is None:
            raise ConsentGovernorError("no autonomy proposal is pending")
        if not self._pending.public.permission_required:
            raise ConsentGovernorError(
                "managed low-risk proposal must use policy authorization"
            )
        decision = classify_consent(reply)
        if decision == "declined":
            return self.decline()
        if decision == "ambiguous":
            return AutonomyAdoption(
                status="ambiguous",
                proposal=self._pending.public,
                reason="explicit yes or no is required",
            )
        return self._approve(
            library,
            authorization_source="explicit_user_permission",
            explicit_consent_observed=True,
        )

    def authorize_managed(self, library: FileSkillLibrary) -> AutonomyAdoption:
        """Auto-authorize only the frozen low-risk, reversible policy envelope."""

        if self._pending is None:
            raise ConsentGovernorError("no autonomy proposal is pending")
        proposal = self._pending.public
        if not managed_auto_authorization_eligible(
            proposal, approval_mode=self.approval_mode
        ):
            raise ConsentGovernorError(
                "proposal is outside the managed auto-authorization envelope"
            )
        return self._approve(
            library,
            authorization_source="managed_low_risk_policy",
            explicit_consent_observed=False,
        )

    def _approve(
        self,
        library: FileSkillLibrary,
        *,
        authorization_source: str,
        explicit_consent_observed: bool,
    ) -> AutonomyAdoption:
        """Cross the approved boundary and adopt only a fully verified COW overlay."""

        if self._pending is None:
            raise ConsentGovernorError("no autonomy proposal is pending")
        pending = self._pending
        proposal = pending.public
        existing = tuple(library.list())
        current_snapshot = active_library_snapshot(existing)[1]
        if current_snapshot != proposal.source_library_snapshot_sha256:
            self._pending = None
            return AutonomyAdoption(
                status="blocked",
                proposal=proposal,
                reason="source library changed after permission was requested",
            )
        if self.completed_actions >= self.max_actions_per_session:
            self._pending = None
            return AutonomyAdoption(
                status="blocked",
                proposal=proposal,
                reason="session autonomy action budget is exhausted",
            )
        output_root = (
            self.trace_root
            / "autonomy"
            / f"action-{self.completed_actions + 1:04d}-{proposal.candidate_skill_id}"
        )
        managed_proposal, draft = _todo_contract(
            proposal=proposal, existing_skills=existing
        )
        try:
            result = run_managed_creation(
                proposal=managed_proposal,
                draft=draft,
                existing_skills=existing,
                output_root=output_root,
            )
        except (ManagedCreationError, OSError, ValueError) as exc:
            self._pending = None
            self._write_decision(
                output_root,
                proposal=proposal,
                status="blocked",
                reason=type(exc).__name__,
                source_unchanged=(active_library_snapshot(tuple(library.list()))[1] == current_snapshot),
                authorization_source=authorization_source,
                explicit_consent_observed=explicit_consent_observed,
            )
            return AutonomyAdoption(
                status="blocked",
                proposal=proposal,
                reason="verification rejected the proposed change",
            )
        source_unchanged = active_library_snapshot(tuple(library.list()))[1] == current_snapshot
        if not result.adopted or not source_unchanged:
            self._pending = None
            self._write_decision(
                output_root,
                proposal=proposal,
                status="blocked",
                reason="adoption_gate_or_source_isolation_failed",
                source_unchanged=source_unchanged,
                authorization_source=authorization_source,
                explicit_consent_observed=explicit_consent_observed,
            )
            return AutonomyAdoption(
                status="blocked",
                proposal=proposal,
                reason="adoption or source-isolation gate failed",
            )
        overlay = FileSkillLibrary(output_root / "provisional-library")
        bundles = {proposal.candidate_skill_id: output_root / "candidate" / proposal.candidate_skill_id}
        self.completed_actions += 1
        self._pending = None
        evidence = {
            **result.to_dict(),
            "authority_policy_version": POLICY_VERSION,
            "approval_mode": self.approval_mode,
            "authorization_source": authorization_source,
            "explicit_consent_observed": explicit_consent_observed,
            "request_sha256": proposal.request_sha256,
            "request_stored": False,
            "source_library_unchanged": True,
            "provider_calls_for_skill_change": 0,
            "ordinary_chat_resume_is_separate": True,
        }
        self._write_decision(
            output_root,
            proposal=proposal,
            status="adopted",
            reason="all_gates_passed",
            source_unchanged=True,
            authorization_source=authorization_source,
            explicit_consent_observed=explicit_consent_observed,
        )
        return AutonomyAdoption(
            status="adopted",
            proposal=proposal,
            library=overlay,
            skill_bundle_paths=bundles,
            creation_evidence=evidence,
        )

    @staticmethod
    def _write_decision(
        output_root: Path,
        *,
        proposal: AutonomyProposal,
        status: GovernorStatus,
        reason: str,
        source_unchanged: bool,
        authorization_source: str,
        explicit_consent_observed: bool,
    ) -> None:
        if not output_root.is_dir():
            return
        path = output_root / "consent_decision.json"
        payload = {
            "schema_version": 1,
            "policy_version": POLICY_VERSION,
            "proposal_id": proposal.proposal_id,
            "request_sha256": proposal.request_sha256,
            "request_stored": False,
            "approval_mode": proposal.approval_mode,
            "risk_class": proposal.risk_class,
            "authorization_source": authorization_source,
            "explicit_consent_observed": explicit_consent_observed,
            "status": status,
            "reason": reason,
            "source_library_unchanged": source_unchanged,
        }
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        except FileExistsError as exc:
            raise ConsentGovernorError("refusing to overwrite consent evidence") from exc
