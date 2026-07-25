"""Deterministic, auditable provisioning policy for Merlin chat agent.

This policy governs prompt exposure only.  Ranked or provisioned skill IDs are
not evidence that a provider loaded or invoked a skill body.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import LifecycleStatus, SkillArtifact
from .provisioning import tokenize
from .skill_name_governance import (
    NameUniqueProvisioningView,
    build_name_unique_provisioning_view,
)


POLICY_VERSION = "governed-provisioning-v2"
MIN_POSITIVE_SCORE = 0.10
NEGATIVE_GUARD_THRESHOLD = 0.50
MAX_DECISION_QUERY_CHARS = 20_000
_EXPLICIT_FILENAME_RE = re.compile(
    r"(?<![A-Za-z0-9._-])([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9][A-Za-z0-9._-]*)(?![A-Za-z0-9._-])"
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_anchor_evidence(values: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"sha256": _sha256_text(value), "chars": len(value)}
        for value in values
    ]


def _overlap_score(query: str, evidence: str) -> float:
    query_counts = Counter(tokenize(query))
    evidence_counts = Counter(tokenize(evidence))
    if not query_counts or not evidence_counts:
        return 0.0
    overlap = sum(
        min(query_counts[token], evidence_counts[token])
        for token in query_counts.keys() & evidence_counts.keys()
    )
    return min(1.0, overlap / max(1, sum(query_counts.values())))


def explicit_filename_anchors(query: str) -> tuple[str, ...]:
    return tuple(sorted({match.group(1) for match in _EXPLICIT_FILENAME_RE.finditer(query)}))


def _declared_inputs(skill: SkillArtifact) -> set[str]:
    return {
        Path(value).name
        for step in skill.steps
        for value in step.inputs
        if value and Path(value).name
    }


def _declared_artifacts(skill: SkillArtifact) -> set[str]:
    values = set(skill.expected_artifacts)
    for step in skill.steps:
        values.update(step.outputs)
    return {Path(value).name for value in values if value and Path(value).name}


def active_library_snapshot(skills: Iterable[SkillArtifact]) -> tuple[str, str, tuple[str, ...]]:
    active = sorted(
        (skill for skill in skills if skill.status == LifecycleStatus.ACTIVE),
        key=lambda skill: skill.id,
    )
    records = []
    for skill in active:
        body_hash = _sha256_text(_canonical_json(skill.to_dict()))
        records.append(
            {
                "skill_id": skill.id,
                "version": skill.version,
                "status": skill.status.value,
                "body_sha256": body_hash,
            }
        )
    digest = _sha256_text(_canonical_json(records))
    return f"active-{digest[:16]}", digest, tuple(record["skill_id"] for record in records)


@dataclass(frozen=True, slots=True)
class ProvisioningCandidateRecord:
    skill_id: str
    lifecycle_status: str
    artifact_anchor_matches: tuple[str, ...]
    input_anchor_matches: tuple[str, ...]
    positive_trigger_score: float
    positive_description_score: float
    positive_score: float
    negative_score: float
    exact_anchor_evidence: bool
    skillops_contract_fields_present: tuple[str, ...]
    skillops_contract_fields_missing: tuple[str, ...]
    aip_declared_step_count: int
    aip_declared_edge_count: int
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    rank: int | None
    provisioned: bool

    @property
    def provisioning_health(self) -> str:
        return "eligible" if self.eligible else "excluded"

    @property
    def provisioning_action(self) -> str:
        if self.provisioned:
            return "provision_prompt_context"
        if self.eligible:
            return "rank_only_outside_exposure_budget"
        return "exclude_from_prompt_context"

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "lifecycle_status": self.lifecycle_status,
            "artifact_anchor_match_count": len(self.artifact_anchor_matches),
            "artifact_anchor_match_evidence": _safe_anchor_evidence(
                self.artifact_anchor_matches
            ),
            "input_anchor_match_count": len(self.input_anchor_matches),
            "input_anchor_match_evidence": _safe_anchor_evidence(
                self.input_anchor_matches
            ),
            "positive_trigger_score": self.positive_trigger_score,
            "positive_description_score": self.positive_description_score,
            "positive_score": self.positive_score,
            "negative_score": self.negative_score,
            "exact_anchor_evidence": self.exact_anchor_evidence,
            "skillops_contract_fields_present": list(self.skillops_contract_fields_present),
            "skillops_contract_fields_missing": list(self.skillops_contract_fields_missing),
            "aip_declared_step_count": self.aip_declared_step_count,
            "aip_declared_edge_count": self.aip_declared_edge_count,
            "eligible": self.eligible,
            "exclusion_reasons": list(self.exclusion_reasons),
            "rank": self.rank,
            "provisioned": self.provisioned,
            "provisioning_health": self.provisioning_health,
            "provisioning_action": self.provisioning_action,
        }


@dataclass(frozen=True, slots=True)
class GovernedProvisioningDecision:
    policy_version: str
    query_sha256: str
    query_chars: int
    active_library_size: int
    active_library_snapshot_id: str
    active_library_snapshot_sha256: str
    active_skill_ids: tuple[str, ...]
    name_unique_provisioning_view: NameUniqueProvisioningView
    explicit_filename_anchors: tuple[str, ...]
    explicit_artifact_anchors: tuple[str, ...]
    explicit_input_anchors: tuple[str, ...]
    unmatched_explicit_filenames: tuple[str, ...]
    anchor_pool_preferred: bool
    min_positive_score: float
    negative_guard_threshold: float
    candidates: tuple[ProvisioningCandidateRecord, ...]
    ranked_ids: tuple[str, ...]
    provisioned_ids: tuple[str, ...]
    primary_id: str | None
    abstain_reason: str | None

    def candidate(self, skill_id: str) -> ProvisioningCandidateRecord:
        for record in self.candidates:
            if record.skill_id == skill_id:
                return record
        raise KeyError(skill_id)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy_version": self.policy_version,
            "query_sha256": self.query_sha256,
            "query_chars": self.query_chars,
            "query_stored": False,
            "active_library_size": self.active_library_size,
            "active_library_snapshot_id": self.active_library_snapshot_id,
            "active_library_snapshot_sha256": self.active_library_snapshot_sha256,
            "active_skill_ids": list(self.active_skill_ids),
            "name_collision_governance": self.name_unique_provisioning_view.to_safe_dict(),
            "explicit_filename_anchor_count": len(self.explicit_filename_anchors),
            "explicit_filename_anchor_evidence": _safe_anchor_evidence(
                self.explicit_filename_anchors
            ),
            "explicit_artifact_anchor_count": len(self.explicit_artifact_anchors),
            "explicit_artifact_anchor_evidence": _safe_anchor_evidence(
                self.explicit_artifact_anchors
            ),
            "explicit_input_anchor_count": len(self.explicit_input_anchors),
            "explicit_input_anchor_evidence": _safe_anchor_evidence(
                self.explicit_input_anchors
            ),
            "unmatched_explicit_filename_count": len(self.unmatched_explicit_filenames),
            "unmatched_explicit_filename_evidence": _safe_anchor_evidence(
                self.unmatched_explicit_filenames
            ),
            "anchor_pool_preferred": self.anchor_pool_preferred,
            "min_positive_score": self.min_positive_score,
            "negative_guard_threshold": self.negative_guard_threshold,
            "candidates": [record.to_safe_dict() for record in self.candidates],
            "harness_ranked_ids": list(self.ranked_ids),
            "harness_primary_id": self.primary_id,
            "provisioned_ids": list(self.provisioned_ids),
            "abstain_reason": self.abstain_reason,
            "boundary": {
                "ranked_ids_are_harness_decisions": True,
                "provisioned_ids_are_prompt_exposure": True,
                "provider_native_loaded_skill_ids": None,
                "provider_native_invoked_skill_ids": None,
                "actual_invocation_evidence_complete": False,
                "shadowing_measurement_scope": "prompt_exposure_only",
            },
            "research_contract": {
                "skillsbench_matched_no_skill_outcome_available": False,
                "skillsbench_normalized_gain": None,
                "skillsbench_normalized_gain_reason": "no matched task-success outcomes in provisioning decision",
                "skillops_contract": "P/O/A/V/F presence plus read-only provisioning health/action",
                "more_skills_loaded_evidence_available": False,
                "more_skills_invoked_evidence_available": False,
                "aip_anchor_scope": "declared step inputs, outputs, and expected artifacts",
                "deferred_interfaces": [
                    "SkillRevise trace-conditioned revision",
                    "Counterfactual Trace Auditing paired task outcomes",
                    "Self-Harness held-in/held-out promotion",
                    "SkillOS learned curation arm",
                ],
            },
        }


class GovernedProvisioner:
    """Apply a fixed evidence order without an LLM semantic judge."""

    def __init__(
        self,
        *,
        exposure_budget: int = 3,
        min_positive_score: float = MIN_POSITIVE_SCORE,
        negative_guard_threshold: float = NEGATIVE_GUARD_THRESHOLD,
        policy_version: str = POLICY_VERSION,
    ) -> None:
        if isinstance(exposure_budget, bool) or not 1 <= exposure_budget <= 10:
            raise ValueError("exposure_budget must be from 1 through 10")
        if not 0 < min_positive_score <= 1:
            raise ValueError("min_positive_score must be in (0, 1]")
        if not 0 < negative_guard_threshold <= 1:
            raise ValueError("negative_guard_threshold must be in (0, 1]")
        if not policy_version.strip():
            raise ValueError("policy_version must be non-empty")
        self.exposure_budget = exposure_budget
        self.min_positive_score = min_positive_score
        self.negative_guard_threshold = negative_guard_threshold
        self.policy_version = policy_version.strip()

    def decide(
        self,
        query: str,
        skills: Iterable[SkillArtifact],
    ) -> GovernedProvisioningDecision:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        if "\x00" in query or len(query) > MAX_DECISION_QUERY_CHARS:
            raise ValueError(
                f"query must be at most {MAX_DECISION_QUERY_CHARS} characters and contain no NUL"
            )
        query = query.strip()
        all_skills = sorted(list(skills), key=lambda skill: skill.id)
        snapshot_id, snapshot_sha256, active_ids = active_library_snapshot(all_skills)
        name_unique_view = build_name_unique_provisioning_view(all_skills)
        provisionable_active_ids = set(name_unique_view.provisionable_active_skill_ids)
        anchors = explicit_filename_anchors(query)

        evidence: dict[str, dict[str, Any]] = {}
        matched_artifacts: set[str] = set()
        matched_inputs: set[str] = set()
        for skill in all_skills:
            artifact_matches = tuple(sorted(set(anchors) & _declared_artifacts(skill)))
            input_matches = tuple(sorted(set(anchors) & _declared_inputs(skill)))
            if (
                skill.status == LifecycleStatus.ACTIVE
                and skill.id in provisionable_active_ids
            ):
                matched_artifacts.update(artifact_matches)
                matched_inputs.update(input_matches)
            trigger_score = _overlap_score(query, skill.trigger)
            description_score = _overlap_score(query, skill.description)
            negative_score = max(
                (_overlap_score(query, constraint) for constraint in skill.do_not_use_when),
                default=0.0,
            )
            evidence[skill.id] = {
                "artifact_matches": artifact_matches,
                "input_matches": input_matches,
                "trigger_score": trigger_score,
                "description_score": description_score,
                "positive_score": max(trigger_score, description_score),
                "negative_score": negative_score,
            }

        anchor_pool_ids = {
            skill.id
            for skill in all_skills
            if skill.status == LifecycleStatus.ACTIVE
            and skill.id in provisionable_active_ids
            and (evidence[skill.id]["artifact_matches"] or evidence[skill.id]["input_matches"])
        }
        anchor_pool_preferred = bool(anchor_pool_ids)
        preliminary: dict[str, tuple[bool, tuple[str, ...]]] = {}
        for skill in all_skills:
            item = evidence[skill.id]
            reasons: list[str] = []
            if skill.status != LifecycleStatus.ACTIVE:
                reasons.append("lifecycle_status_not_active")
            elif skill.id not in provisionable_active_ids:
                canonical_id = name_unique_view.canonical_for(skill.id)
                reasons.append(
                    "declared_name_collision_suppressed"
                    + (f":{canonical_id}" if canonical_id is not None else "")
                )
            elif anchor_pool_preferred and skill.id not in anchor_pool_ids:
                reasons.append("not_in_exact_anchor_pool")
            has_exact_anchor = bool(item["artifact_matches"] or item["input_matches"])
            if item["positive_score"] < self.min_positive_score and not has_exact_anchor:
                reasons.append(f"positive_evidence_below_{self.min_positive_score:.3f}")
            if item["negative_score"] >= self.negative_guard_threshold:
                reasons.append(f"do_not_use_guard_at_{self.negative_guard_threshold:.3f}")
            preliminary[skill.id] = (not reasons, tuple(reasons))

        eligible = [skill for skill in all_skills if preliminary[skill.id][0]]
        eligible.sort(key=lambda skill: (-evidence[skill.id]["positive_score"], skill.id))
        ranked_ids = tuple(skill.id for skill in eligible)
        provisioned_ids = ranked_ids[: self.exposure_budget]
        ranks = {skill_id: index for index, skill_id in enumerate(ranked_ids, start=1)}

        candidate_records = tuple(
            ProvisioningCandidateRecord(
                skill_id=skill.id,
                lifecycle_status=skill.status.value,
                artifact_anchor_matches=evidence[skill.id]["artifact_matches"],
                input_anchor_matches=evidence[skill.id]["input_matches"],
                positive_trigger_score=round(evidence[skill.id]["trigger_score"], 6),
                positive_description_score=round(evidence[skill.id]["description_score"], 6),
                positive_score=round(evidence[skill.id]["positive_score"], 6),
                negative_score=round(evidence[skill.id]["negative_score"], 6),
                exact_anchor_evidence=bool(
                    evidence[skill.id]["artifact_matches"]
                    or evidence[skill.id]["input_matches"]
                ),
                skillops_contract_fields_present=tuple(
                    field
                    for field, present in (
                        ("P", bool(skill.trigger.strip() or skill.do_not_use_when)),
                        ("O", bool(skill.steps)),
                        ("A", bool(_declared_artifacts(skill))),
                        ("V", bool(skill.validators)),
                        ("F", bool(skill.failure_modes)),
                    )
                    if present
                ),
                skillops_contract_fields_missing=tuple(
                    field
                    for field, present in (
                        ("P", bool(skill.trigger.strip() or skill.do_not_use_when)),
                        ("O", bool(skill.steps)),
                        ("A", bool(_declared_artifacts(skill))),
                        ("V", bool(skill.validators)),
                        ("F", bool(skill.failure_modes)),
                    )
                    if not present
                ),
                aip_declared_step_count=len(skill.steps),
                aip_declared_edge_count=len(skill.edges),
                eligible=preliminary[skill.id][0],
                exclusion_reasons=preliminary[skill.id][1],
                rank=ranks.get(skill.id),
                provisioned=skill.id in provisioned_ids,
            )
            for skill in all_skills
        )

        if provisioned_ids:
            abstain_reason = None
        elif not active_ids:
            abstain_reason = "no_active_skills"
        elif anchor_pool_preferred:
            abstain_reason = "no_anchor_candidate_passed_evidence_guards"
        else:
            abstain_reason = "no_candidate_met_minimum_evidence"
        matched = matched_artifacts | matched_inputs
        return GovernedProvisioningDecision(
            policy_version=self.policy_version,
            query_sha256=_sha256_text(query),
            query_chars=len(query),
            active_library_size=len(active_ids),
            active_library_snapshot_id=snapshot_id,
            active_library_snapshot_sha256=snapshot_sha256,
            active_skill_ids=active_ids,
            name_unique_provisioning_view=name_unique_view,
            explicit_filename_anchors=anchors,
            explicit_artifact_anchors=tuple(sorted(matched_artifacts)),
            explicit_input_anchors=tuple(sorted(matched_inputs)),
            unmatched_explicit_filenames=tuple(sorted(set(anchors) - matched)),
            anchor_pool_preferred=anchor_pool_preferred,
            min_positive_score=self.min_positive_score,
            negative_guard_threshold=self.negative_guard_threshold,
            candidates=candidate_records,
            ranked_ids=ranked_ids,
            provisioned_ids=provisioned_ids,
            primary_id=ranked_ids[0] if ranked_ids else None,
            abstain_reason=abstain_reason,
        )
