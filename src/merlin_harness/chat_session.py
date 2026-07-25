"""Chat-session orchestration with turn-local skill provisioning and health."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .codex_chat import CodexChatTurnResult
from .governed_provisioning import GovernedProvisioner, GovernedProvisioningDecision
from .harnessx_chat_shadow import HarnessXChatShadow
from .library import FileSkillLibrary
from .models import LifecycleStatus, SkillArtifact
from .provisioning import LexicalProvisioner, lexical_score
from .semantic_router import (
    SemanticRouterError,
    SemanticRouterErrorCode,
    SemanticRouterResult,
    SemanticSkillRouter,
    validate_router_result,
)


MAX_USER_INPUT_CHARS = 20_000
_PROVIDER_THREAD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ChatSessionError(RuntimeError):
    """A safe user-facing chat session error."""


class ChatTurnBackend(Protocol):
    def run_turn(
        self, *, prompt: str, turn_number: int, thread_id: str | None
    ) -> CodexChatTurnResult: ...


@dataclass(frozen=True, slots=True)
class ProvisionedSkill:
    skill_id: str
    name: str
    score: float
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "score": self.score,
            "why": self.why,
        }


@dataclass(frozen=True, slots=True)
class ChatResponse:
    answer: str
    thread_id: str
    turn_id: str | None
    turn_number: int
    provisioned_skills: tuple[ProvisionedSkill, ...]
    routing_decision: Mapping[str, Any]
    raw_trace_pointer: str


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ChatSessionError(f"refusing to overwrite ledger artifact: {path.name}") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


class TheKingChatSession:
    """Provision active skills before every provider-backed chat turn."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        library: FileSkillLibrary,
        backend: ChatTurnBackend,
        trace_root: str | Path,
        top_k: int = 3,
        per_skill_context_chars: int = 1200,
        total_skill_context_chars: int = 3600,
        routing_mode: str = "deterministic",
        semantic_router: SemanticSkillRouter | None = None,
        skill_bundle_paths: Mapping[str, str | Path] | None = None,
        harnessx_shadow: HarnessXChatShadow | None = None,
    ) -> None:
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise ValueError("workspace must be an existing directory")
        trace_path = Path(trace_root).expanduser().resolve()
        if not trace_path.is_dir() or not trace_path.is_relative_to(workspace_path):
            raise ValueError("trace_root must be an existing directory inside workspace")
        if isinstance(top_k, bool) or not 1 <= top_k <= 10:
            raise ValueError("top_k must be from 1 through 10")
        if per_skill_context_chars < 200:
            raise ValueError("per_skill_context_chars must be at least 200")
        if total_skill_context_chars < per_skill_context_chars:
            raise ValueError("total_skill_context_chars must cover at least one skill")
        if routing_mode not in {"semantic", "deterministic", "controlled_lexical"}:
            raise ValueError(
                "routing_mode must be semantic, deterministic, or controlled_lexical"
            )
        if routing_mode == "semantic" and semantic_router is None:
            raise ValueError("semantic routing mode requires a semantic_router")

        self.workspace = workspace_path
        self.library = library
        self.backend = backend
        self.trace_root = trace_path
        self.top_k = top_k
        self.per_skill_context_chars = per_skill_context_chars
        self.total_skill_context_chars = total_skill_context_chars
        self.provisioner = GovernedProvisioner(exposure_budget=top_k)
        self.controlled_lexical_provisioner = LexicalProvisioner(
            exposure_budget=top_k
        )
        self.routing_mode = routing_mode
        self.semantic_router = semantic_router
        self.skill_bundle_paths = self._validate_skill_bundle_paths(
            library, skill_bundle_paths or {}
        )
        self.harnessx_shadow = harnessx_shadow
        self.thread_id: str | None = None
        self._next_turn_number = 1
        self._completed: list[dict[str, Any]] = []
        self._feedback_by_turn: dict[int, str] = {}
        self._new_thread_count = 0

    def _validate_skill_bundle_paths(
        self,
        library: FileSkillLibrary,
        paths: Mapping[str, str | Path],
    ) -> dict[str, Path]:
        known_ids = {skill.id for skill in library.list()}
        validated: dict[str, Path] = {}
        for skill_id, raw_path in paths.items():
            path = Path(raw_path).expanduser().resolve()
            if (
                skill_id not in known_ids
                or not path.is_dir()
                or not path.is_relative_to(self.workspace)
            ):
                raise ValueError(
                    "skill bundle paths must name library skills inside the chat workspace"
                )
            validated[skill_id] = path
        return validated

    def install_verified_library_overlay(
        self,
        *,
        library: FileSkillLibrary,
        skill_bundle_paths: Mapping[str, str | Path],
    ) -> None:
        """Atomically replace the session library after external gate verification."""

        validated = self._validate_skill_bundle_paths(library, skill_bundle_paths)
        self.library = library
        self.skill_bundle_paths = validated

    def _active_skills(self) -> list[SkillArtifact]:
        return self.library.list(status=LifecycleStatus.ACTIVE)

    def list_skills(self) -> list[dict[str, Any]]:
        return [
            {
                "skill_id": skill.id,
                "name": skill.name,
                "status": skill.status.value,
                "trigger": skill.trigger,
            }
            for skill in self.library.list()
        ]

    def _provision(
        self,
        user_input: str,
        *,
        turn_number: int,
        explicit_skill_id: str | None = None,
    ) -> tuple[
        list[SkillArtifact],
        list[ProvisionedSkill],
        GovernedProvisioningDecision,
        dict[str, Any],
    ]:
        all_skills = self.library.list()
        by_id = {skill.id: skill for skill in all_skills}
        decision = self.provisioner.decide(user_input, all_skills)
        final_ids = decision.provisioned_ids
        routing: dict[str, Any] = {
            "schema_version": 2,
            "routing_mode": self.routing_mode,
            "routing_source": "deterministic",
            "query_sha256": decision.query_sha256,
            "query_chars": decision.query_chars,
            "query_stored": False,
            "active_skill_count": decision.active_library_size,
            "candidate_skill_count": decision.name_unique_provisioning_view.provisionable_active_count,
            "candidate_skill_ids": list(
                decision.name_unique_provisioning_view.provisionable_active_skill_ids
            ),
            "name_collision_policy_version": (
                decision.name_unique_provisioning_view.policy_version
            ),
            "name_collision_group_count": len(
                decision.name_unique_provisioning_view.collision_groups
            ),
            "name_collision_suppressed_ids": list(
                decision.name_unique_provisioning_view.suppressed_skill_ids
            ),
            "anchor_pool_preferred": decision.anchor_pool_preferred,
            "semantic_ranked_ids": [],
            "semantic_negative_excluded_ids": [],
            "semantic_abstained": False,
            "deterministic_guard_excluded_ids": [],
            "final_provisioned_ids": list(final_ids),
            "final_abstain_reason": decision.abstain_reason,
            "authoritative_final_decision": True,
            "fallback_error_class": None,
            "model_call_skipped_no_active_skills": False,
            "requested_model_id": None,
            "requested_effort": None,
            "provider_reported_model_ids": [],
            "raw_trace": None,
            "ranked_ids_are_prompt_exposure_not_invocation": True,
        }
        semantic_result: SemanticRouterResult | None = None
        if explicit_skill_id is not None:
            provisionable_ids = set(
                decision.name_unique_provisioning_view.provisionable_active_skill_ids
            )
            skill = by_id.get(explicit_skill_id)
            if skill is None:
                raise ChatSessionError("the explicitly requested skill does not exist")
            if skill.status != LifecycleStatus.ACTIVE:
                raise ChatSessionError("the explicitly requested skill is not active")
            if explicit_skill_id not in provisionable_ids:
                raise ChatSessionError(
                    "the explicitly requested skill is blocked by name-collision governance"
                )
            final_ids = (explicit_skill_id,)
            routing["routing_source"] = "explicit_skill"
            routing["candidate_skill_count"] = 1
            routing["candidate_skill_ids"] = [explicit_skill_id]
            routing["anchor_pool_preferred"] = False
            routing["final_abstain_reason"] = None
            routing["explicit_skill_id"] = explicit_skill_id
        elif self.routing_mode == "controlled_lexical":
            controlled_skills = [
                by_id[skill_id]
                for skill_id in decision.name_unique_provisioning_view.provisionable_active_skill_ids
            ]
            controlled = self.controlled_lexical_provisioner.provision(
                user_input,
                controlled_skills,
            )
            final_ids = tuple(skill.id for skill in controlled)
            routing["routing_source"] = "controlled_lexical"
            routing["anchor_pool_preferred"] = False
            routing["final_abstain_reason"] = (
                None if final_ids else "no_positive_lexical_evidence"
            )
        elif self.routing_mode == "semantic":
            assert self.semantic_router is not None
            routing["requested_model_id"] = self.semantic_router.model_id
            routing["requested_effort"] = self.semantic_router.effort
            active = [skill for skill in all_skills if skill.status == LifecycleStatus.ACTIVE]
            provisionable_ids = set(
                decision.name_unique_provisioning_view.provisionable_active_skill_ids
            )
            active = [skill for skill in active if skill.id in provisionable_ids]
            if decision.anchor_pool_preferred:
                anchor_ids = {
                    candidate.skill_id
                    for candidate in decision.candidates
                    if candidate.lifecycle_status == LifecycleStatus.ACTIVE.value
                    and candidate.exact_anchor_evidence
                }
                candidates = [skill for skill in active if skill.id in anchor_ids]
            else:
                candidates = active
            routing["candidate_skill_count"] = len(candidates)
            routing["candidate_skill_ids"] = [skill.id for skill in candidates]
            if not candidates:
                final_ids = ()
                routing["routing_source"] = "semantic_abstain"
                routing["semantic_abstained"] = True
                routing["final_abstain_reason"] = "no_active_skills" if not active else "no_anchor_candidates"
                routing["model_call_skipped_no_active_skills"] = not active
            else:
                try:
                    semantic_result = self.semantic_router.route(
                        query=user_input,
                        skills=candidates,
                        exposure_budget=self.top_k,
                        turn_number=turn_number,
                    )
                    semantic_result = validate_router_result(
                        semantic_result,
                        skills=candidates,
                        exposure_budget=self.top_k,
                        expected_model_id=self.semantic_router.model_id,
                        expected_effort=self.semantic_router.effort,
                        trace_root=self.trace_root,
                    )
                except SemanticRouterError as exc:
                    error_code = exc.code
                    if decision.anchor_pool_preferred and error_code in {
                        SemanticRouterErrorCode.UNKNOWN_SKILL_ID,
                        SemanticRouterErrorCode.INACTIVE_SKILL_ID,
                    }:
                        error_code = SemanticRouterErrorCode.ANCHOR_CONFLICT
                    routing["routing_source"] = "deterministic_fallback"
                    routing["fallback_error_class"] = error_code.value
                    final_ids = decision.provisioned_ids
                    routing["final_abstain_reason"] = decision.abstain_reason
                else:
                    routing["routing_source"] = (
                        "semantic_abstain" if semantic_result.abstained else "semantic"
                    )
                    routing["semantic_ranked_ids"] = list(semantic_result.ranked_ids)
                    routing["semantic_negative_excluded_ids"] = list(
                        semantic_result.negative_excluded_ids
                    )
                    routing["semantic_abstained"] = semantic_result.abstained
                    if semantic_result.abstained:
                        routing["final_abstain_reason"] = "semantic_router_abstained"
                    routing["provider_reported_model_ids"] = list(
                        semantic_result.provider_reported_model_ids
                    )
                    if semantic_result.raw_trace_pointer and semantic_result.raw_trace_sha256:
                        routing["raw_trace"] = {
                            "pointer": semantic_result.raw_trace_pointer,
                            "sha256": semantic_result.raw_trace_sha256,
                        }
                    guard_excluded: list[str] = []
                    kept: list[str] = []
                    for skill_id in semantic_result.ranked_ids:
                        candidate = decision.candidate(skill_id)
                        if candidate.negative_score >= decision.negative_guard_threshold:
                            guard_excluded.append(skill_id)
                        else:
                            kept.append(skill_id)
                    routing["deterministic_guard_excluded_ids"] = guard_excluded
                    final_ids = () if semantic_result.abstained else tuple(kept[: self.top_k])
                    if not semantic_result.abstained and not final_ids:
                        routing["routing_source"] = "semantic_abstain"
                        routing["final_abstain_reason"] = (
                            "all_semantic_ranked_ids_blocked_by_deterministic_guard"
                        )
                    elif final_ids:
                        routing["final_abstain_reason"] = None
        routing["final_provisioned_ids"] = list(final_ids)
        selected = [by_id[skill_id] for skill_id in final_ids]
        records: list[ProvisionedSkill] = []
        for skill in selected:
            candidate = decision.candidate(skill.id)
            notes: list[str] = []
            if candidate.artifact_anchor_matches:
                notes.append(
                    f"artifact anchor match count={len(candidate.artifact_anchor_matches)}"
                )
            if candidate.input_anchor_matches:
                notes.append(f"input anchor match count={len(candidate.input_anchor_matches)}")
            anchor_note = f"; {'; '.join(notes)}" if notes else ""
            records.append(
                ProvisionedSkill(
                    skill_id=skill.id,
                    name=skill.name,
                    score=(
                        lexical_score(user_input, skill)
                        if self.routing_mode == "controlled_lexical"
                        else (
                            1.0
                            if semantic_result is not None
                            and skill.id in semantic_result.ranked_ids
                            else candidate.positive_score
                        )
                    ),
                    why=(
                        (
                            "controlled-naive-lexical-v1; "
                            f"lexical score={lexical_score(user_input, skill):.3f}; "
                            "prompt exposure only"
                        )
                        if self.routing_mode == "controlled_lexical"
                        else (
                            f"semantic rank={semantic_result.ranked_ids.index(skill.id) + 1}; "
                            f"deterministic negative={candidate.negative_score:.3f}{anchor_note}"
                            if semantic_result is not None
                            and skill.id in semantic_result.ranked_ids
                            else (
                                f"{decision.policy_version}; positive score={candidate.positive_score:.3f}; "
                                f"trigger={candidate.positive_trigger_score:.3f}; "
                                f"description={candidate.positive_description_score:.3f}; "
                                f"negative={candidate.negative_score:.3f}{anchor_note}"
                            )
                        )
                    ),
                )
            )
        return selected, records, decision, routing

    def _skill_block(self, skill: SkillArtifact) -> str:
        steps = "\n".join(f"- {step.description}" for step in skill.steps)
        blocked = "; ".join(skill.do_not_use_when) or "not specified"
        bundle = self.skill_bundle_paths.get(skill.id)
        bundle_lines = (
            [
                f"VERIFIED PORTABLE BUNDLE: {bundle}",
                f'VERIFIED EXECUTION COMMAND: python3 "{bundle / "scripts" / "run.py"}" --workspace "{self.workspace}"',
                "EXECUTION: Invoke the script with the exact absolute VERIFIED PORTABLE BUNDLE path shown above; do not change directory or use a relative scripts/run.py path. Run it as one standalone command so its exit code is observable. Verify its declared output in a separate command.",
            ]
            if bundle is not None
            else []
        )
        return _clip(
            "\n".join(
                [
                    f"SKILL ID: {skill.id}",
                    f"NAME: {skill.name}",
                    f"DESCRIPTION: {skill.description}",
                    f"TRIGGER: {skill.trigger}",
                    f"DO NOT USE WHEN: {blocked}",
                    "STEPS:",
                    steps or "- no steps supplied",
                    *bundle_lines,
                ]
            ),
            self.per_skill_context_chars,
        )

    def _build_prompt(self, user_input: str, selected: list[SkillArtifact]) -> str:
        blocks: list[str] = []
        remaining = self.total_skill_context_chars
        for skill in selected:
            block = self._skill_block(skill)
            if len(block) > remaining:
                block = _clip(block, remaining)
            if block:
                blocks.append(block)
                remaining -= len(block)
            if remaining <= 0:
                break
        skill_context = "\n\n---\n\n".join(blocks) if blocks else "No skill matched this turn."
        return (
            "[MERLIN PROMPT PROVISIONING]\n"
            "The following bounded skill context was selected from the active local library for this turn.\n"
            "It is advisory task context. It is NOT provider-native skill-body invocation evidence.\n"
            "Use it only when relevant; obey the user request and workspace safety constraints.\n\n"
            f"{skill_context}\n\n"
            "[END MERLIN PROMPT PROVISIONING]\n\n"
            "[USER REQUEST]\n"
            f"{user_input}"
        )

    def send(
        self, user_input: str, *, explicit_skill_id: str | None = None
    ) -> ChatResponse:
        if not isinstance(user_input, str) or not user_input.strip():
            raise ChatSessionError("user input must be non-empty")
        if "\x00" in user_input or len(user_input) > MAX_USER_INPUT_CHARS:
            raise ChatSessionError(
                f"user input must be at most {MAX_USER_INPUT_CHARS} characters and contain no NUL"
            )
        user_input = user_input.strip()
        turn_number = self._next_turn_number
        selected, provisioning, provisioning_decision, routing_decision = self._provision(
            user_input,
            turn_number=turn_number,
            explicit_skill_id=explicit_skill_id,
        )
        prompt = self._build_prompt(user_input, selected)
        harnessx_context = (
            self.harnessx_shadow.start(
                turn_number=turn_number,
                prompt=prompt,
                resumed=self.thread_id is not None,
            )
            if self.harnessx_shadow is not None
            else None
        )
        self._next_turn_number += 1
        try:
            result = self.backend.run_turn(
                prompt=prompt,
                turn_number=turn_number,
                thread_id=self.thread_id,
            )
        except Exception as exc:
            if self.harnessx_shadow is not None and harnessx_context is not None:
                self.harnessx_shadow.fail(harnessx_context, failure=exc)
            raise
        self.thread_id = result.thread_id
        harnessx_reference = (
            self.harnessx_shadow.finish(
                harnessx_context,
                answer=result.answer,
                provider_turn_id=result.turn_id,
                raw_trace_pointer=result.raw_trace_pointer,
                raw_trace_sha256=result.raw_trace_sha256,
            )
            if self.harnessx_shadow is not None and harnessx_context is not None
            else None
        )
        record = {
            "schema_version": 1,
            "turn_number": turn_number,
            "provider_thread_id": result.thread_id,
            "provider_turn_id": result.turn_id,
            "resumed": result.resumed,
            "user_input_sha256": _sha256_text(user_input),
            "user_input_chars": len(user_input),
            "user_input_stored": False,
            "assistant_answer_sha256": _sha256_text(result.answer),
            "assistant_answer_chars": len(result.answer),
            "assistant_answer_stored": False,
            "provisioned_skills": [item.to_dict() for item in provisioning],
            "deterministic_reference_decision": provisioning_decision.to_safe_dict(),
            "routing_decision": routing_decision,
            "prompt_provisioning_is_provider_native_invocation": False,
            "actual_invocation_evidence_complete": False,
            "raw_trace": {
                "pointer": result.raw_trace_pointer,
                "sha256": result.raw_trace_sha256,
            },
            "backend_metadata": result.metadata,
            "feedback_status": "pending",
            "lifecycle_automatic_change": "deferred",
        }
        if harnessx_reference is not None:
            record["harnessx_shadow"] = harnessx_reference.to_dict()
        _write_new_json(self.trace_root / f"turn-{turn_number:04d}.meta.json", record)
        self._completed.append(record)
        return ChatResponse(
            answer=result.answer,
            thread_id=result.thread_id,
            turn_id=result.turn_id,
            turn_number=turn_number,
            provisioned_skills=tuple(provisioning),
            routing_decision=routing_decision,
            raw_trace_pointer=result.raw_trace_pointer,
        )

    def start_new_thread(self) -> None:
        self.thread_id = None
        self._new_thread_count += 1

    def prepare_thread_resume(self, thread_id: str) -> None:
        """Prepare a retained Codex thread for the next provider turn.

        The next ``send`` is the verification boundary: Codex must accept the
        identifier and return a non-conflicting thread. No provider success is
        claimed merely because this local state transition succeeds.
        """

        if self._completed or self.thread_id is not None:
            raise ChatSessionError("thread resume requires a fresh local session")
        if not isinstance(thread_id, str) or not _PROVIDER_THREAD_RE.fullmatch(thread_id):
            raise ChatSessionError("provider thread_id has an unsafe format")
        self.thread_id = thread_id

    def record_feedback(self, outcome: str) -> dict[str, Any]:
        if outcome not in {"pass", "fail"}:
            raise ChatSessionError("feedback outcome must be 'pass' or 'fail'")
        if not self._completed:
            raise ChatSessionError("no completed turn is available for feedback")
        turn = self._completed[-1]
        turn_number = int(turn["turn_number"])
        if turn_number in self._feedback_by_turn:
            raise ChatSessionError("feedback for the latest turn is already recorded")
        payload = {
            "schema_version": 1,
            "turn_number": turn_number,
            "outcome": outcome,
            "raw_trace": turn["raw_trace"],
            "provisioned_skill_ids": [
                item["skill_id"] for item in turn["provisioned_skills"]
            ],
            "automatic_lifecycle_change": False,
            "lifecycle_note": "feedback is health evidence only; automatic hide/repair remains deferred",
        }
        _write_new_json(self.trace_root / f"feedback-turn-{turn_number:04d}.json", payload)
        self._feedback_by_turn[turn_number] = outcome
        return payload

    def last_trace(self) -> dict[str, Any] | None:
        if not self._completed:
            return None
        record = dict(self._completed[-1])
        turn_number = int(record["turn_number"])
        record["feedback_status"] = self._feedback_by_turn.get(turn_number, "pending")
        return record

    def status(self) -> dict[str, Any]:
        exposure_counts: dict[str, int] = {}
        for turn in self._completed:
            for item in turn["provisioned_skills"]:
                skill_id = item["skill_id"]
                exposure_counts[skill_id] = exposure_counts.get(skill_id, 0) + 1
        pass_count = sum(outcome == "pass" for outcome in self._feedback_by_turn.values())
        fail_count = sum(outcome == "fail" for outcome in self._feedback_by_turn.values())
        routing_source_counts: dict[str, int] = {}
        for turn in self._completed:
            source = str(turn.get("routing_decision", {}).get("routing_source", "unknown"))
            routing_source_counts[source] = routing_source_counts.get(source, 0) + 1
        skill_contracts = [
            {
                "id": skill.id,
                "name": skill.name,
                "status": skill.status.value,
                "description": skill.description,
                "trigger": skill.trigger,
                "version": skill.version,
                "validators": list(skill.validators),
                "step_count": len(skill.steps),
                "edge_count": len(skill.edges),
                "expected_artifacts": list(skill.expected_artifacts),
                "failure_modes": list(skill.failure_modes),
            }
            for skill in self.library.list()
        ]
        return {
            "schema_version": 1,
            "provider_thread_active": self.thread_id is not None,
            "provider_thread_id": self.thread_id,
            "completed_turns": len(self._completed),
            "new_thread_count": self._new_thread_count,
            "active_skill_count": len(self._active_skills()),
            "no_skill_turns": sum(not turn["provisioned_skills"] for turn in self._completed),
            "skill_exposure_counts": exposure_counts,
            "routing_mode": self.routing_mode,
            "routing_source_counts": routing_source_counts,
            "feedback": {
                "pass": pass_count,
                "fail": fail_count,
                "pending": len(self._completed) - len(self._feedback_by_turn),
            },
            "selection_health": "observed_only",
            "automatic_lifecycle_changes": "deferred",
            "harnessx_shadow": {
                "enabled": self.harnessx_shadow is not None,
                "mode": "shadow_only" if self.harnessx_shadow is not None else "disabled",
                "evidence_turns": sum(
                    "harnessx_shadow" in turn for turn in self._completed
                ),
                "candidate_outputs_applied": False,
                "tool_hooks_synthesized": False,
            },
            "controlled_lifecycle_debugger": (
                "available via /governance in the terminal and experiments.mvp.run_console"
            ),
            # Contract metadata is declared library state, not evidence of a
            # provider-native invocation or lifecycle transition.
            "skill_contracts": skill_contracts,
        }
