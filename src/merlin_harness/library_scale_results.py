"""Fail-closed validation and aggregation for library-scale run cells.

This module consumes normalized agent traces, not selector logs.  It can report
verifier/reward scaling from scored cells, but invocation event curves require
complete provider-observed skill loads plus a separately supplied empirical
oracle mapping.  The current curated-reference manifest has no empirical
oracle-only arm, so More Skills ``Delta_ctx``/``Delta_shd`` decomposition stays
ineligible even after event curves become available.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .metrics import (
    InvocationObservation,
    MoreSkillsDecomposition,
    more_skills_decomposition,
    oracle_invocation_event_summary,
)
from .models import TraceRecord
from .traces import validate_agent_trace_evidence


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_OUTCOME_STATUSES = {
    "scored_verifier",
    "model_noncompletion_timeout_zero",
    "infrastructure_unscored",
}


class LibraryScaleResultError(ValueError):
    """Raised when a library-scale result is not bound to frozen evidence."""


@dataclass(frozen=True, slots=True)
class ValidatedLibraryScaleCell:
    cell_id: str
    task_id: str
    trial_index: int
    arm_id: str
    library_size: int
    outcome_status: str
    verifier_passed: bool | None
    reward: float | None
    actual_invocation_evidence_complete: bool
    invoked_skill_ids: tuple[str, ...]
    selected_skill_ids: tuple[str, ...]
    staged_verifier_tree_sha256: str
    runtime_key: tuple[str, str, str, str, str | None, str, str]
    raw_trace_sha256: str


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise LibraryScaleResultError(f"{label} must be a lowercase SHA-256")
    return value


def _require_exact_sequence(value: Any, expected: Sequence[str], *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise LibraryScaleResultError(f"{label} must be an ordered string sequence")
    result = tuple(value)
    if result != tuple(expected):
        raise LibraryScaleResultError(f"{label} does not match the frozen presentation order")
    return result


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def validate_library_scale_cell_trace(
    *,
    manifest_cell: Mapping[str, Any],
    materialization_contract: Mapping[str, Any],
    trace: TraceRecord,
    verify_raw_trace: bool = True,
) -> ValidatedLibraryScaleCell:
    """Bind one normalized trace to its manifest cell and staged byte contract."""

    cell_id = manifest_cell.get("cell_id")
    task_id = manifest_cell.get("task_id")
    arm_id = manifest_cell.get("arm_id")
    trial_index = manifest_cell.get("trial_index")
    library_size = manifest_cell.get("library_size")
    frozen_order = manifest_cell.get("library_variant_ids")
    if (
        not isinstance(cell_id, str)
        or not isinstance(task_id, str)
        or not isinstance(arm_id, str)
        or isinstance(trial_index, bool)
        or not isinstance(trial_index, int)
        or isinstance(library_size, bool)
        or not isinstance(library_size, int)
        or not isinstance(frozen_order, list)
    ):
        raise LibraryScaleResultError("manifest cell has an invalid result contract")

    for field_name, expected in (
        ("cell_id", cell_id),
        ("task_id", task_id),
        ("trial_index", trial_index),
        ("arm_id", arm_id),
        ("library_size", library_size),
        ("manifest_library_snapshot_sha256", manifest_cell.get("library_snapshot_sha256")),
        ("task_instruction_sha256", manifest_cell.get("task_instruction_sha256")),
        ("verifier_contract_sha256", manifest_cell.get("verifier_contract_sha256")),
    ):
        if materialization_contract.get(field_name) != expected:
            raise LibraryScaleResultError(
                f"materialization contract {field_name} does not match manifest cell"
            )
    if materialization_contract.get("source_and_staged_bytes_match") is not True:
        raise LibraryScaleResultError("materialization contract does not prove source/staged byte equality")
    byte_snapshot = _require_sha256(
        materialization_contract.get("materialized_byte_snapshot_sha256"),
        label="materialized byte snapshot",
    )
    presentation_order = _require_exact_sequence(
        materialization_contract.get("presentation_order"),
        frozen_order,
        label="materialized presentation order",
    )
    if len(presentation_order) != library_size:
        raise LibraryScaleResultError("materialized presentation order size mismatch")
    variant_records = materialization_contract.get("variant_records")
    if not isinstance(variant_records, list) or len(variant_records) != library_size:
        raise LibraryScaleResultError("materialization variant records do not cover the library")
    for ordinal, (record, expected_variant) in enumerate(
        zip(variant_records, presentation_order, strict=True),
        start=1,
    ):
        if not isinstance(record, dict):
            raise LibraryScaleResultError("materialization variant record must be an object")
        if record.get("ordinal") != ordinal or record.get("variant") != expected_variant:
            raise LibraryScaleResultError("materialization variant record order mismatch")
        source_sha = _require_sha256(
            record.get("source_tree_sha256"),
            label="source skill tree hash",
        )
        staged_sha = _require_sha256(
            record.get("staged_tree_sha256"),
            label="staged skill tree hash",
        )
        if source_sha != staged_sha:
            raise LibraryScaleResultError("materialization source/staged skill hashes differ")
    if _sha256_json(variant_records) != byte_snapshot:
        raise LibraryScaleResultError("materialized byte snapshot does not match variant records")

    evidence = validate_agent_trace_evidence(trace, verify_raw_trace=verify_raw_trace)
    contract = evidence.contract
    if trace.id != cell_id:
        raise LibraryScaleResultError("normalized trace id must equal cell id")
    if trace.task_id != task_id or contract.task_id != task_id:
        raise LibraryScaleResultError("trace task does not match manifest cell")
    if trace.condition != cell_id or contract.condition != cell_id:
        raise LibraryScaleResultError("trace condition must equal the frozen cell id")
    if contract.library_snapshot_id != cell_id:
        raise LibraryScaleResultError("agent contract library_snapshot_id must equal cell id")
    if contract.library_snapshot_sha256 != byte_snapshot:
        raise LibraryScaleResultError("agent contract library snapshot does not match staged bytes")
    if trace.invocation is None:
        raise LibraryScaleResultError("library-scale trace requires an invocation envelope")
    harness_mode = trace.metadata.get("harness_mode")
    if harness_mode == "metadata-first-staged-body-v1":
        provisioned = trace.invocation.provisioned_skill_ids
        if (
            not isinstance(provisioned, list)
            or any(not isinstance(skill_id, str) for skill_id in provisioned)
            or len(provisioned) != len(set(provisioned))
            or set(provisioned) - set(presentation_order)
        ):
            raise LibraryScaleResultError(
                "metadata-first provisioned skills must be a unique staged-library subset"
            )
        exposure_budget = trace.metadata.get("exposure_budget")
        if (
            isinstance(exposure_budget, bool)
            or not isinstance(exposure_budget, int)
            or not 1 <= exposure_budget <= 10
            or len(provisioned) > exposure_budget
        ):
            raise LibraryScaleResultError("metadata-first exposure budget is invalid")
        if trace.metadata.get("candidate_library_order_sha256") != _sha256_json(
            presentation_order
        ):
            raise LibraryScaleResultError("metadata-first candidate library order drifted")
    else:
        _require_exact_sequence(
            trace.invocation.provisioned_skill_ids,
            presentation_order,
            label="trace provisioned skills",
        )
    if trace.invocation.oracle_skill_ids:
        raise LibraryScaleResultError(
            "raw library-scale trace must not embed curated or unverified oracle IDs"
        )

    verifier_contract_sha = _require_sha256(
        manifest_cell.get("verifier_contract_sha256"),
        label="manifest verifier contract",
    )
    if contract.verifier_id != verifier_contract_sha:
        raise LibraryScaleResultError("agent verifier id does not match manifest verifier contract")
    staged_verifier_sha = _require_sha256(
        trace.metadata.get("staged_verifier_tree_sha256"),
        label="staged verifier tree hash",
    )
    if trace.metadata.get("verifier_contract_sha256") != verifier_contract_sha:
        raise LibraryScaleResultError("trace verifier contract hash does not match manifest")

    outcome_status = trace.metadata.get("outcome_status")
    if outcome_status not in _ALLOWED_OUTCOME_STATUSES:
        raise LibraryScaleResultError("trace outcome_status is missing or unsupported")
    if not isinstance(harness_mode, str) or not harness_mode.strip():
        raise LibraryScaleResultError("trace harness_mode must be non-empty")
    if len(trace.validation) != 1:
        raise LibraryScaleResultError("library-scale trace requires exactly one verifier result")
    validation = trace.validation[0]
    reward = validation.score
    if reward is not None:
        if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not 0.0 <= reward <= 1.0:
            raise LibraryScaleResultError("verifier reward must be numeric in [0,1] or null")
        reward = float(reward)
    if outcome_status == "infrastructure_unscored":
        if reward is not None or trace.invocation.success is not None:
            raise LibraryScaleResultError("infrastructure_unscored cells must have null outcome and reward")
        verifier_passed: bool | None = None
    else:
        if reward is None or not isinstance(validation.passed, bool):
            raise LibraryScaleResultError("scored cells require a numeric reward and boolean verifier outcome")
        verifier_passed = validation.passed
        if trace.invocation.success is not verifier_passed:
            raise LibraryScaleResultError("invocation success does not match verifier outcome")
        if trace.invocation.score is None or float(trace.invocation.score) != reward:
            raise LibraryScaleResultError("invocation score does not match verifier reward")
        if outcome_status == "model_noncompletion_timeout_zero" and (
            reward != 0.0 or verifier_passed
        ):
            raise LibraryScaleResultError("timeout-zero cells must be an explicit scored failure")

    invoked_ids = tuple(dict.fromkeys(event.skill_id for event in evidence.invocation_events))
    unknown_selected = set(evidence.selected_skill_ids) - set(presentation_order)
    if unknown_selected:
        raise LibraryScaleResultError(
            "selected skill evidence contains skills outside the staged library"
        )
    unknown_invoked = set(invoked_ids) - set(presentation_order)
    if unknown_invoked:
        raise LibraryScaleResultError(
            "actual invocation evidence contains skills outside the staged library"
        )
    return ValidatedLibraryScaleCell(
        cell_id=cell_id,
        task_id=task_id,
        trial_index=trial_index,
        arm_id=arm_id,
        library_size=library_size,
        outcome_status=outcome_status,
        verifier_passed=verifier_passed,
        reward=reward,
        actual_invocation_evidence_complete=evidence.actual_invocation_evidence_complete,
        invoked_skill_ids=invoked_ids,
        selected_skill_ids=evidence.selected_skill_ids,
        staged_verifier_tree_sha256=staged_verifier_sha,
        runtime_key=(
            contract.agent_id,
            contract.agent_version,
            contract.backend,
            contract.model_id,
            contract.effort,
            contract.budget_id,
            harness_mode,
        ),
        raw_trace_sha256=evidence.raw_trace.sha256,
    )


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _event_summary_payload(summary) -> dict[str, Any]:
    return {
        "eligible": summary.eligible,
        "excluded_no_oracle": summary.excluded_no_oracle,
        "counts": summary.counts,
        "event_probabilities": {
            name: {
                "numerator": rate.numerator,
                "denominator": rate.denominator,
                "value": rate.value,
            }
            for name, rate in summary.event_probabilities.items()
        },
        "conditional_pass_rates": {
            name: {
                "numerator": rate.numerator,
                "denominator": rate.denominator,
                "value": rate.value,
            }
            for name, rate in summary.conditional_pass_rates.items()
        },
    }


def _decomposition_payload(value: MoreSkillsDecomposition) -> dict[str, Any]:
    return {
        "p_oracle": value.p_oracle,
        "p_library": value.p_library,
        "observed_drop": value.observed_drop,
        "delta_ctx": value.delta_ctx,
        "delta_shd": value.delta_shd,
        "total": value.total,
        "invariant_error": value.invariant_error,
        "invariant_holds": value.invariant_holds,
        "unavailable_reason": value.unavailable_reason,
    }


def aggregate_library_scale_cells(
    *,
    manifest: Mapping[str, Any],
    cells: Sequence[ValidatedLibraryScaleCell],
    empirical_oracle_by_task: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Aggregate validated cells while keeping unavailable claims explicit."""

    manifest_cells = manifest.get("cells")
    if not isinstance(manifest_cells, list):
        raise LibraryScaleResultError("manifest cells must be a list")
    expected = {cell.get("cell_id"): cell for cell in manifest_cells}
    if None in expected or len(expected) != len(manifest_cells):
        raise LibraryScaleResultError("manifest cell IDs must be unique and non-empty")
    observed: dict[str, ValidatedLibraryScaleCell] = {}
    for cell in cells:
        if cell.cell_id not in expected:
            raise LibraryScaleResultError(f"result references unknown cell: {cell.cell_id}")
        if cell.cell_id in observed:
            raise LibraryScaleResultError(f"duplicate result cell: {cell.cell_id}")
        frozen = expected[cell.cell_id]
        for field_name in ("task_id", "trial_index", "arm_id", "library_size"):
            if getattr(cell, field_name) != frozen.get(field_name):
                raise LibraryScaleResultError(
                    f"validated result {field_name} does not match manifest cell: {cell.cell_id}"
                )
        frozen_library = frozen.get("library_variant_ids")
        if not isinstance(frozen_library, list):
            raise LibraryScaleResultError(
                f"manifest cell library is invalid: {cell.cell_id}"
            )
        if set(cell.selected_skill_ids) - set(frozen_library):
            raise LibraryScaleResultError(
                f"validated selected skills escape the staged library: {cell.cell_id}"
            )
        if set(cell.invoked_skill_ids) - set(frozen_library):
            raise LibraryScaleResultError(
                f"validated invoked skills escape the staged library: {cell.cell_id}"
            )
        observed[cell.cell_id] = cell

    verifier_hashes: dict[str, set[str]] = defaultdict(set)
    runtime_keys: set[tuple[str, str, str, str, str | None, str, str]] = set()
    for cell in cells:
        verifier_hashes[cell.task_id].add(cell.staged_verifier_tree_sha256)
        runtime_keys.add(cell.runtime_key)
    drifted_tasks = sorted(task_id for task_id, hashes in verifier_hashes.items() if len(hashes) != 1)
    if drifted_tasks:
        raise LibraryScaleResultError(
            "staged verifier tree drift across paired arms: " + ", ".join(drifted_tasks)
        )
    if len(runtime_keys) > 1:
        raise LibraryScaleResultError("runtime agent/backend/effort/budget drift across cells")

    arm_ids = []
    for manifest_cell in manifest_cells:
        arm_id = manifest_cell["arm_id"]
        if arm_id not in arm_ids:
            arm_ids.append(arm_id)
    operational: dict[str, Any] = {}
    for arm_id in arm_ids:
        arm_expected = [cell for cell in manifest_cells if cell["arm_id"] == arm_id]
        arm_observed = [cell for cell in cells if cell.arm_id == arm_id]
        scored = [cell for cell in arm_observed if cell.reward is not None]
        rewards = [cell.reward for cell in scored if cell.reward is not None]
        passed = sum(cell.verifier_passed is True for cell in scored)
        operational[arm_id] = {
            "scheduled_cells": len(arm_expected),
            "observed_cells": len(arm_observed),
            "scored_cells": len(scored),
            "passed_cells": passed,
            "pass_rate": _rate(passed, len(scored)),
            "mean_reward": sum(rewards) / len(rewards) if rewards else None,
            "actual_invocation_complete_cells": sum(
                cell.actual_invocation_evidence_complete for cell in arm_observed
            ),
            "any_actual_invocation_rate": _rate(
                sum(bool(cell.invoked_skill_ids) for cell in arm_observed),
                len(arm_observed),
            ),
            "outcome_status_counts": dict(sorted(Counter(cell.outcome_status for cell in arm_observed).items())),
        }

    missing_cell_ids = sorted(set(expected) - set(observed))
    full_denominator_observed = not missing_cell_ids
    full_denominator_scored = full_denominator_observed and all(cell.reward is not None for cell in cells)
    invocation_complete = full_denominator_observed and all(
        cell.actual_invocation_evidence_complete for cell in cells
    )
    shadowing: dict[str, Any]
    if not full_denominator_observed:
        shadowing = {
            "status": "unavailable",
            "reason": f"full {len(manifest_cells):,}-cell denominator is incomplete",
            "event_curves": None,
        }
    elif not full_denominator_scored:
        shadowing = {
            "status": "unavailable",
            "reason": "one or more full-denominator cells are unscored infrastructure outcomes",
            "event_curves": None,
        }
    elif not invocation_complete:
        shadowing = {
            "status": "unavailable",
            "reason": "actual invocation evidence is incomplete for one or more cells",
            "event_curves": None,
        }
    elif empirical_oracle_by_task is None:
        shadowing = {
            "status": "unavailable",
            "reason": "no separately estimated empirical oracle mapping was supplied",
            "event_curves": None,
        }
    else:
        task_ids = {cell["task_id"] for cell in manifest_cells}
        if set(empirical_oracle_by_task) != task_ids:
            raise LibraryScaleResultError("empirical oracle mapping must cover all manifest tasks exactly")
        task_contracts = {
            item.get("task_id"): item
            for item in manifest.get("task_contracts", [])
            if isinstance(item, dict)
        }
        if set(task_contracts) != task_ids:
            raise LibraryScaleResultError("manifest task contracts must cover all tasks exactly")
        normalized_oracles: dict[str, tuple[str, ...]] = {}
        for task_id in sorted(task_ids):
            value = empirical_oracle_by_task[task_id]
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(skill_id, str) or not skill_id for skill_id in value
            ):
                raise LibraryScaleResultError(
                    f"empirical oracle mapping for {task_id} must be a string sequence"
                )
            oracle_ids = tuple(value)
            if len(oracle_ids) != len(set(oracle_ids)):
                raise LibraryScaleResultError(
                    f"empirical oracle mapping for {task_id} contains duplicates"
                )
            reference = set(task_contracts[task_id].get("reference_skill_variants", []))
            outside = set(oracle_ids) - reference
            if outside:
                raise LibraryScaleResultError(
                    f"empirical oracle mapping for {task_id} is outside the always-present curated candidate scope"
                )
            normalized_oracles[task_id] = oracle_ids
        event_curves: dict[str, Any] = {}
        event_summaries = {}
        for arm_id in arm_ids:
            observations = [
                InvocationObservation(
                    task_id=cell.task_id,
                    invoked_skill_ids=cell.invoked_skill_ids,
                    oracle_skill_ids=normalized_oracles[cell.task_id],
                    success=cell.verifier_passed,
                )
                for cell in cells
                if cell.arm_id == arm_id
            ]
            summary = oracle_invocation_event_summary(observations)
            event_summaries[arm_id] = summary
            event_curves[arm_id] = _event_summary_payload(summary)
        if "oracle-only" not in arm_ids:
            shadowing = {
                "status": "available_event_curves_only",
                "reason": None,
                "event_curves": event_curves,
                "more_skills_decomposition": None,
                "more_skills_decomposition_eligible": False,
                "decomposition_blocker": (
                    "current reference arm is the upstream curated bundle, not an empirical oracle-only arm"
                ),
            }
        else:
            evidence_contract = manifest.get("evidence_contract")
            if (
                manifest.get("schema_version") != 2
                or not isinstance(evidence_contract, dict)
                or evidence_contract.get("empirical_oracle_bound") is not True
            ):
                raise LibraryScaleResultError(
                    "oracle-only arm requires an empirical-oracle-bound schema 2 manifest"
                )
            for manifest_cell in manifest_cells:
                task_id = manifest_cell["task_id"]
                declared_oracle = manifest_cell.get(
                    "empirical_oracle_skill_variants"
                )
                if declared_oracle != list(normalized_oracles[task_id]):
                    raise LibraryScaleResultError(
                        f"manifest empirical oracle binding drifted for {task_id}"
                    )
                if manifest_cell["arm_id"] == "oracle-only":
                    library_ids = manifest_cell.get("library_variant_ids")
                    if (
                        not isinstance(library_ids, list)
                        or len(library_ids) != len(set(library_ids))
                        or set(library_ids) != set(normalized_oracles[task_id])
                    ):
                        raise LibraryScaleResultError(
                            f"oracle-only cell does not equal the empirical oracle set for {task_id}"
                        )
            oracle_summary = event_summaries["oracle-only"]
            decompositions = {
                arm_id: more_skills_decomposition(
                    oracle_summary,
                    event_summaries[arm_id],
                )
                for arm_id in arm_ids
                if arm_id != "oracle-only"
            }
            unavailable = {
                arm_id: value.unavailable_reason
                for arm_id, value in decompositions.items()
                if value.unavailable_reason is not None
                or value.invariant_holds is not True
            }
            decomposition_eligible = not unavailable
            shadowing = {
                "status": (
                    "available_with_decomposition"
                    if decomposition_eligible
                    else "available_event_curves_only"
                ),
                "reason": None,
                "event_curves": event_curves,
                "more_skills_decomposition": {
                    arm_id: _decomposition_payload(value)
                    for arm_id, value in decompositions.items()
                },
                "more_skills_decomposition_eligible": decomposition_eligible,
                "decomposition_blocker": (
                    None
                    if decomposition_eligible
                    else "one or more oracle-only comparisons are unavailable or violate the decomposition invariant: "
                    + ", ".join(sorted(unavailable))
                ),
            }

    return {
        "schema_version": 1,
        "experiment_id": manifest.get("experiment_id"),
        "expected_cells": len(manifest_cells),
        "observed_cells": len(cells),
        "missing_cell_count": len(missing_cell_ids),
        "missing_cell_ids": missing_cell_ids,
        "full_denominator_observed": full_denominator_observed,
        "full_denominator_scored": full_denominator_scored,
        "actual_invocation_evidence_complete": invocation_complete,
        "runtime_contract_count": len(runtime_keys),
        "operational_summary": operational,
        "shadowing_summary": shadowing,
    }
