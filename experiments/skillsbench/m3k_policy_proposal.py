"""Canonical pre-registered M3-K policy proposal.

This is a hypothesis input, not a benchmark result. It uses only the frozen
controlled overload diagnostic; no full-87 held-out task, verifier, oracle, or
outcome participates in candidate construction.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.merlin_harness.harness import (
    HarnessEvolutionProposal,
    HarnessVariantSpec,
    Hook,
    build_runtime_from_variant,
    make_default_harness_runtime,
    snapshot_harness_variant,
)
from src.merlin_harness.management import content_sha256


class M3KPolicyProposalError(ValueError):
    pass


BUNDLE_CONTRACT = "merlin-m3k-preregistered-policy-proposal-v1"
EVIDENCE_SOURCE_PATH = "experiments/mvp/results/lifecycle_recovery/lifecycle_recovery.json"
EVIDENCE_FILE_SHA256 = "da8a407971f070216e726a4d9f005dff737a683211801f93b319421faaa5ffce"
EVIDENCE_SEMANTIC_SHA256 = "01ffe9a9590012bcb42a1d88d6bdfe88223791b9ebb8af6e2bfe8fe8a835f30a"
EVIDENCE_TRACE_IDS = (
    "overloaded_library-create-audit-log-20ae45c3",
    "overloaded_library-create-notes-file-f91723ea",
    "overloaded_library-create-output-json-15255aca",
    "overloaded_library-create-report-md-0efe41c0",
    "overloaded_library-create-result-file-6f8fcdb6",
    "overloaded_library-count-errors-aa3182ac",
    "overloaded_library-count-items-26227aec",
    "overloaded_library-count-records-24411535",
)
TRACE_IDS_SHA256 = "de14f995e5f6e278af69f1895fb8cf6d7e2c304d2e587b01b6428eb8a1ec0973"
PARENT_ID = "m3k-full87-m2k-parent-budget10-v1"
CANDIDATE_ID = "m3k-full87-candidate-budget3-v1"
PROPOSAL_ID = "m3k-full87-exposure-budget-proposal-v1"
PARENT_BUDGET = 10
CANDIDATE_BUDGET = 3
RATIONALE = (
    "Pre-register a bounded exposure-budget reduction after controlled route-risk "
    "evidence showed overload shadowing; the 3-skill candidate is a hypothesis, "
    "not a full-87 or held-out-tuned result."
)
SOURCE_SCOPE = "Deterministic controlled MVP; not a full benchmark or model-performance claim."


def _metrics(*, recovered: bool) -> dict[str, Any]:
    return (
        {
            "task_count": 10,
            "passed": 9,
            "pass_rate": 0.9,
            "pi_o": 1.0,
            "pi_m": 0.0,
            "route_counts": {"empty_no_oracle": 1, "oracle_only": 9},
        }
        if recovered
        else {
            "task_count": 10,
            "passed": 1,
            "pass_rate": 0.1,
            "pi_o": 1.0 / 9.0,
            "pi_m": 8.0 / 9.0,
            "route_counts": {"empty_no_oracle": 1, "oracle_only": 1, "wrong": 8},
        }
    )


def _project(value: Any) -> dict[str, Any]:
    keys = ("task_count", "passed", "pass_rate", "pi_o", "pi_m", "route_counts")
    if not isinstance(value, dict) or any(key not in value for key in keys):
        raise M3KPolicyProposalError("controlled evidence condition is incomplete")
    return {key: value[key] for key in keys}


def controlled_evidence_projection(
    payload: dict[str, Any], *, file_sha256: str
) -> dict[str, Any]:
    if file_sha256 != EVIDENCE_FILE_SHA256:
        raise M3KPolicyProposalError("controlled evidence file SHA-256 drifted")
    if content_sha256(payload) != EVIDENCE_SEMANTIC_SHA256:
        raise M3KPolicyProposalError("controlled evidence semantic SHA-256 drifted")
    if payload.get("schema_version") != 2 or payload.get("scope") != SOURCE_SCOPE:
        raise M3KPolicyProposalError("controlled evidence scope drifted")
    conditions = payload.get("conditions")
    if not isinstance(conditions, dict):
        raise M3KPolicyProposalError("controlled evidence conditions are missing")
    overload = _project(conditions.get("Overloaded library"))
    recovered = _project(conditions.get("Lifecycle recovered"))
    if overload != _metrics(recovered=False) or recovered != _metrics(recovered=True):
        raise M3KPolicyProposalError("controlled evidence metrics drifted")
    decisions = payload.get("lifecycle_decisions")
    if not isinstance(decisions, list) or len(decisions) != 2:
        raise M3KPolicyProposalError("controlled lifecycle decisions drifted")
    trace_ids: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("action") != "hide":
            raise M3KPolicyProposalError("controlled lifecycle action drifted")
        ids = decision.get("evidence_trace_ids")
        if not isinstance(ids, list):
            raise M3KPolicyProposalError("controlled evidence trace IDs are missing")
        trace_ids.extend(ids)
    if tuple(trace_ids) != EVIDENCE_TRACE_IDS or content_sha256(trace_ids) != TRACE_IDS_SHA256:
        raise M3KPolicyProposalError("controlled route-risk trace set drifted")
    return {
        "source_path": EVIDENCE_SOURCE_PATH,
        "source_file_sha256": EVIDENCE_FILE_SHA256,
        "source_semantic_sha256": EVIDENCE_SEMANTIC_SHA256,
        "source_scope": SOURCE_SCOPE,
        "source_is_model_execution": False,
        "source_is_full87_result": False,
        "overload": overload,
        "recovered": recovered,
        "route_risk_trace_ids": trace_ids,
        "route_risk_trace_ids_sha256": TRACE_IDS_SHA256,
        "full87_held_out_task_ids_used_for_construction": [],
        "full87_verifier_or_oracle_used_for_construction": False,
    }


def _variants() -> tuple[HarnessVariantSpec, HarnessVariantSpec]:
    parent = snapshot_harness_variant(
        make_default_harness_runtime(max_exposure_budget=PARENT_BUDGET),
        variant_id=PARENT_ID,
        summary="M2-K parent with the runner's bounded 10-skill exposure ceiling",
        metadata={"arm": "M2-K-parent", "bundle_contract": BUNDLE_CONTRACT},
    )
    candidate = snapshot_harness_variant(
        make_default_harness_runtime(max_exposure_budget=CANDIDATE_BUDGET),
        variant_id=CANDIDATE_ID,
        parent_id=parent.id,
        summary="M3-K candidate that clamps model-visible exposure to three skills",
        metadata={
            "arm": "M3-K-candidate",
            "bundle_contract": BUNDLE_CONTRACT,
            "candidate_is_performance_result": False,
            "full87_held_out_used_for_construction": False,
        },
    )
    return parent, candidate


def build_canonical_bundle(
    evidence: dict[str, Any], *, evidence_file_sha256: str
) -> dict[str, Any]:
    construction = controlled_evidence_projection(evidence, file_sha256=evidence_file_sha256)
    parent, candidate = _variants()
    proposal = HarnessEvolutionProposal(
        id=PROPOSAL_ID,
        parent_variant_id=parent.id,
        candidate=candidate,
        rationale=RATIONALE,
        changed_hooks=[Hook.BEFORE_PROVISION.value],
        evidence_trace_ids=list(EVIDENCE_TRACE_IDS),
    )
    bundle = {
        "schema_version": 2,
        "bundle_contract": BUNDLE_CONTRACT,
        "parent_variant": asdict(parent),
        "proposal": {
            "id": proposal.id,
            "parent_variant_id": proposal.parent_variant_id,
            "candidate": asdict(candidate),
            "rationale": proposal.rationale,
            "changed_hooks": proposal.changed_hooks,
            "evidence_trace_ids": proposal.evidence_trace_ids,
        },
        "construction_evidence": construction,
        "candidate_hypothesis": {
            "control_dimension": "exposure_budget",
            "parent_value": PARENT_BUDGET,
            "candidate_value": CANDIDATE_BUDGET,
            "selection_basis": "controlled-overload-informed; not full87-held-out-tuned",
        },
        "claim_boundary": {
            "bundle_creation_is_model_execution": False,
            "bundle_creation_is_benchmark_result": False,
            "candidate_is_promoted": False,
            "full87_result_claimed": False,
            "held_out_improvement_claimed": False,
            "provider_native_skill_invocation_claimed": False,
        },
    }
    validate_canonical_bundle(bundle)
    return bundle


def validate_canonical_bundle(payload: dict[str, Any]) -> None:
    if set(payload) != {
        "schema_version", "bundle_contract", "parent_variant", "proposal",
        "construction_evidence", "candidate_hypothesis", "claim_boundary",
    } or payload.get("schema_version") != 2 or payload.get("bundle_contract") != BUNDLE_CONTRACT:
        raise M3KPolicyProposalError("canonical proposal bundle schema drifted")
    parent, candidate = _variants()
    expected_proposal = {
        "id": PROPOSAL_ID,
        "parent_variant_id": PARENT_ID,
        "candidate": asdict(candidate),
        "rationale": RATIONALE,
        "changed_hooks": [Hook.BEFORE_PROVISION.value],
        "evidence_trace_ids": list(EVIDENCE_TRACE_IDS),
    }
    if payload.get("parent_variant") != asdict(parent) or payload.get("proposal") != expected_proposal:
        raise M3KPolicyProposalError("canonical parent/candidate proposal drifted")
    construction = payload.get("construction_evidence")
    expected_construction = {
        "source_path": EVIDENCE_SOURCE_PATH,
        "source_file_sha256": EVIDENCE_FILE_SHA256,
        "source_semantic_sha256": EVIDENCE_SEMANTIC_SHA256,
        "source_scope": SOURCE_SCOPE,
        "source_is_model_execution": False,
        "source_is_full87_result": False,
        "overload": _metrics(recovered=False),
        "recovered": _metrics(recovered=True),
        "route_risk_trace_ids": list(EVIDENCE_TRACE_IDS),
        "route_risk_trace_ids_sha256": TRACE_IDS_SHA256,
        "full87_held_out_task_ids_used_for_construction": [],
        "full87_verifier_or_oracle_used_for_construction": False,
    }
    if construction != expected_construction:
        raise M3KPolicyProposalError("canonical construction evidence drifted")
    if payload.get("candidate_hypothesis") != {
        "control_dimension": "exposure_budget", "parent_value": 10,
        "candidate_value": 3,
        "selection_basis": "controlled-overload-informed; not full87-held-out-tuned",
    }:
        raise M3KPolicyProposalError("candidate hypothesis drifted")
    boundary = payload.get("claim_boundary")
    if not isinstance(boundary, dict) or not boundary or any(value is not False for value in boundary.values()):
        raise M3KPolicyProposalError("canonical proposal claim boundary is unsafe")
    build_runtime_from_variant(parent)
    build_runtime_from_variant(candidate)
