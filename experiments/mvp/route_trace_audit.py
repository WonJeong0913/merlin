"""Bounded, observe-only audit for user-supplied skill exposure traces."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


MIN_TRACE_RECORDS = 2
MAX_TRACE_RECORDS = 20
MAX_SKILLS_PER_RECORD = 8
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")


class RouteTraceAuditError(ValueError):
    """A safe validation failure for the public trace-audit contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise RouteTraceAuditError(
            "invalid_trace_bundle",
            f"{field} must be a 1-96 character portable identifier.",
        )
    return value


def _require_skill_ids(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_SKILLS_PER_RECORD:
        raise RouteTraceAuditError(
            "invalid_trace_bundle",
            f"{field} must be a list of at most {MAX_SKILLS_PER_RECORD} skill IDs.",
        )
    result = tuple(_require_id(item, field=f"{field}[]") for item in value)
    if len(set(result)) != len(result):
        raise RouteTraceAuditError("invalid_trace_bundle", f"{field} must not contain duplicates.")
    return result


def _route_class(exposed: tuple[str, ...], oracle: tuple[str, ...]) -> str:
    if not oracle:
        return "no_oracle"
    if not exposed:
        return "no_skill"
    exposed_set = set(exposed)
    oracle_set = set(oracle)
    if exposed_set.issubset(oracle_set):
        return "clean_oracle"
    if exposed_set & oracle_set:
        return "mixed"
    return "wrong"


def sample_trace_bundle() -> dict[str, Any]:
    """Return a tiny portable example that produces one repeated-risk candidate."""

    return {
        "schema_version": 1,
        "evidence_level": "prompt_exposure",
        "records": [
            {
                "trace_id": "trace-001",
                "task_id": "summarize-a",
                "exposed_skill_ids": ["quick-summary"],
                "oracle_skill_ids": ["structured-summary"],
                "verifier_passed": False,
            },
            {
                "trace_id": "trace-002",
                "task_id": "summarize-b",
                "exposed_skill_ids": ["quick-summary", "structured-summary"],
                "oracle_skill_ids": ["structured-summary"],
                "verifier_passed": False,
            },
            {
                "trace_id": "trace-003",
                "task_id": "extract-a",
                "exposed_skill_ids": ["structured-extract"],
                "oracle_skill_ids": ["structured-extract"],
                "verifier_passed": True,
            },
        ],
    }


def audit_route_trace_bundle(bundle: Any, *, min_repeated_failures: int = 2) -> dict[str, Any]:
    """Validate and diagnose a small exposure-trace bundle without mutating a library."""

    if isinstance(min_repeated_failures, bool) or not isinstance(min_repeated_failures, int):
        raise RouteTraceAuditError("invalid_threshold", "min_repeated_failures must be an integer.")
    if not 2 <= min_repeated_failures <= 5:
        raise RouteTraceAuditError("invalid_threshold", "min_repeated_failures must be from 2 through 5.")
    if not isinstance(bundle, dict) or set(bundle) != {"schema_version", "evidence_level", "records"}:
        raise RouteTraceAuditError(
            "invalid_trace_bundle",
            "Trace bundle fields must be schema_version, evidence_level, and records.",
        )
    if bundle["schema_version"] != 1:
        raise RouteTraceAuditError("invalid_trace_bundle", "schema_version must be 1.")
    if bundle["evidence_level"] != "prompt_exposure":
        raise RouteTraceAuditError(
            "invalid_trace_bundle",
            "evidence_level must be prompt_exposure; selection is not claimed as invocation.",
        )
    raw_records = bundle["records"]
    if not isinstance(raw_records, list) or not MIN_TRACE_RECORDS <= len(raw_records) <= MAX_TRACE_RECORDS:
        raise RouteTraceAuditError(
            "invalid_trace_bundle",
            f"records must contain {MIN_TRACE_RECORDS} through {MAX_TRACE_RECORDS} items.",
        )

    records: list[dict[str, Any]] = []
    seen_trace_ids: set[str] = set()
    route_counts = {key: 0 for key in ("clean_oracle", "mixed", "wrong", "no_skill", "no_oracle")}
    repeated_failures: dict[str, list[str]] = {}
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict) or set(raw) != {
            "trace_id", "task_id", "exposed_skill_ids", "oracle_skill_ids", "verifier_passed"
        }:
            raise RouteTraceAuditError(
                "invalid_trace_bundle",
                f"records[{index}] fields do not match the trace contract.",
            )
        trace_id = _require_id(raw["trace_id"], field=f"records[{index}].trace_id")
        if trace_id in seen_trace_ids:
            raise RouteTraceAuditError("invalid_trace_bundle", "trace_id values must be unique.")
        seen_trace_ids.add(trace_id)
        task_id = _require_id(raw["task_id"], field=f"records[{index}].task_id")
        exposed = _require_skill_ids(raw["exposed_skill_ids"], field=f"records[{index}].exposed_skill_ids")
        oracle = _require_skill_ids(raw["oracle_skill_ids"], field=f"records[{index}].oracle_skill_ids")
        if not isinstance(raw["verifier_passed"], bool):
            raise RouteTraceAuditError(
                "invalid_trace_bundle",
                f"records[{index}].verifier_passed must be boolean.",
            )
        route = _route_class(exposed, oracle)
        route_counts[route] += 1
        if not raw["verifier_passed"] and route in {"mixed", "wrong"}:
            for skill_id in sorted(set(exposed) - set(oracle)):
                repeated_failures.setdefault(skill_id, []).append(trace_id)
        records.append(
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "exposed_skill_ids": list(exposed),
                "oracle_skill_ids": list(oracle),
                "verifier_passed": raw["verifier_passed"],
                "route_class": route,
            }
        )

    eligible = len(records) - route_counts["no_oracle"]
    shadowed = route_counts["mixed"] + route_counts["wrong"]
    candidates = [
        {
            "skill_id": skill_id,
            "provisional_action": "hide",
            "failed_route_count": len(trace_ids),
            "evidence_trace_ids": trace_ids,
        }
        for skill_id, trace_ids in sorted(repeated_failures.items())
        if len(trace_ids) >= min_repeated_failures
    ]
    canonical_input = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1,
        "audit": "Merlin user-supplied route trace audit",
        "input_sha256": hashlib.sha256(canonical_input).hexdigest(),
        "evidence_boundary": {
            "level": "prompt_exposure",
            "provider_native_invocation_claimed": False,
            "task_success_causality_claimed": False,
        },
        "metrics": {
            "record_count": len(records),
            "eligible_oracle_records": eligible,
            "verifier_passed": sum(record["verifier_passed"] for record in records),
            "route_counts": route_counts,
            "exposure_shadowing_rate": shadowed / eligible if eligible else None,
        },
        "records": records,
        "diagnosis": {
            "min_repeated_failures": min_repeated_failures,
            "provisional_candidates": candidates,
            "candidate_count": len(candidates),
        },
        "safety": {
            "observe_only": True,
            "source_library_mutated": False,
            "promotion_allowed": False,
            "required_next_gate": "stage copy-on-write and re-run the same tasks and verifiers",
        },
    }
