"""Independently audit the GPT-5.6 selection-only shadowing pilot raw chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from experiments.skillsbench.run_gpt56_selection_shadowing_pilot import (
    ALLOWED_ITEM_TYPES,
    ARM_SIZES,
    PILOT_ID,
    REPO_ROOT,
    TASK_IDS,
    TRIAL_INDICES,
    SelectionPilotError,
    _arm,
    _canonical_json,
    _item_types,
    _presentation,
    _sha256_bytes,
    _sha256_text,
    build_plan,
    build_prompt,
    parse_response,
    response_schema,
    validate_report,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl


class SelectionPilotAuditError(ValueError):
    """Raised when raw provider evidence differs from the safe report."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionPilotAuditError(f"invalid JSON audit input: {path.name}") from exc
    if not isinstance(value, dict):
        raise SelectionPilotAuditError(f"audit input must be an object: {path.name}")
    return value


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise SelectionPilotAuditError(f"refusing to overwrite audit output: {path.name}") from exc


def _require_hash(value: object, *, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SelectionPilotAuditError(f"{label} is not a SHA-256")
    return value


def audit_pilot(
    *, raw_root: Path, report_path: Path, output_path: Path
) -> dict[str, Any]:
    raw_root = raw_root.expanduser().resolve()
    report_path = report_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve(strict=False)
    if raw_root.is_relative_to(REPO_ROOT):
        raise SelectionPilotAuditError("raw provider root must stay outside the repository")
    if output_path.exists() or output_path.is_symlink():
        raise SelectionPilotAuditError("safe audit output must be new-only")
    report = _read_json(report_path)
    plan = build_plan()
    try:
        safe_validation = validate_report(report, plan=plan)
    except SelectionPilotError as exc:
        raise SelectionPilotAuditError(str(exc)) from exc

    expected_dirs = {
        f"{arm_id}__t{trial}" for arm_id, _size in ARM_SIZES for trial in TRIAL_INDICES
    }
    actual_dirs = {path.name for path in raw_root.iterdir() if path.is_dir()}
    if actual_dirs != expected_dirs:
        raise SelectionPilotAuditError("raw cell directory set differs from the frozen plan")
    by_id = {cell["cell_id"]: cell for cell in report["cells"]}
    audited_cells: list[dict[str, Any]] = []
    for cell_id in sorted(expected_dirs):
        cell = by_id[cell_id]
        arm_id = cell["arm_id"]
        trial = cell["trial_index"]
        arm = _arm(plan, arm_id)
        cell_root = raw_root / cell_id
        raw_path = cell_root / "provider.codex.jsonl"
        schema_path = cell_root / "response.schema.json"
        last_message_path = cell_root / "provider.last-message.json"
        if not raw_path.is_file() or raw_path.is_symlink():
            raise SelectionPilotAuditError(f"raw trace is missing or linked: {cell_id}")
        if not schema_path.is_file() or schema_path.is_symlink():
            raise SelectionPilotAuditError(f"response schema is missing or linked: {cell_id}")
        raw_bytes = raw_path.read_bytes()
        if _sha256_bytes(raw_bytes) != _require_hash(cell["raw_trace_sha256"], label="raw hash"):
            raise SelectionPilotAuditError(f"raw trace hash drifted: {cell_id}")
        expected_schema = (
            json.dumps(
                response_schema(plan, arm_id=arm_id),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if schema_path.read_bytes() != expected_schema:
            raise SelectionPilotAuditError(f"response schema bytes drifted: {cell_id}")
        if _sha256_bytes(expected_schema) != cell["schema_sha256"]:
            raise SelectionPilotAuditError(f"response schema hash drifted: {cell_id}")
        prompt = build_prompt(plan, arm_id=arm_id, trial_index=trial)
        if _sha256_text(prompt) != cell["prompt_sha256"]:
            raise SelectionPilotAuditError(f"reconstructed prompt hash drifted: {cell_id}")
        if _sha256_text(_canonical_json(_presentation(arm, trial))) != cell["presentation_sha256"]:
            raise SelectionPilotAuditError(f"presentation order hash drifted: {cell_id}")
        try:
            raw_text = raw_bytes.decode("utf-8")
            summary = parse_codex_exec_jsonl(raw_text)
            item_types = _item_types(raw_text)
        except (UnicodeDecodeError, CodexCliAdapterError, SelectionPilotError) as exc:
            raise SelectionPilotAuditError(f"raw provider trace parse failed: {cell_id}") from exc
        if list(summary.reported_model_ids) != cell["provider_reported_model_ids"]:
            raise SelectionPilotAuditError(f"provider-reported model fields drifted: {cell_id}")
        if list(item_types) != cell["item_types"] or set(item_types) - ALLOWED_ITEM_TYPES:
            raise SelectionPilotAuditError(f"provider item types drifted: {cell_id}")
        response = summary.final_message
        if response is None and last_message_path.is_file():
            response = last_message_path.read_text(encoding="utf-8").strip()
        if not response or _sha256_text(response.strip()) != cell["response_sha256"]:
            raise SelectionPilotAuditError(f"provider response hash drifted: {cell_id}")
        parsed = parse_response(
            response.strip(),
            task_ids=TASK_IDS,
            allowed_skill_ids=frozenset(arm["skill_ids"]),
        )
        recorded = [
            {"task_id": item["task_id"], "selected_skill_id": item["selected_skill_id"]}
            for item in cell["decisions"]
        ]
        if list(parsed) != recorded:
            raise SelectionPilotAuditError(f"provider selections differ from safe report: {cell_id}")
        audited_cells.append(
            {
                "cell_id": cell_id,
                "raw_trace_sha256": cell["raw_trace_sha256"],
                "prompt_sha256": cell["prompt_sha256"],
                "schema_sha256": cell["schema_sha256"],
                "response_sha256": cell["response_sha256"],
                "selection_count": len(parsed),
                "provider_tool_execution_observed": False,
            }
        )

    checks = {
        "safe_report_revalidated": safe_validation["passed"] is True,
        "plan_and_source_bindings_reconstructed": report["plan_sha256"] == plan["plan_sha256"],
        "raw_cell_set_exact": len(audited_cells) == 8,
        "raw_trace_hashes_exact": True,
        "strict_schemas_exact": True,
        "prompts_reconstructed_by_hash": True,
        "provider_item_types_no_tool": True,
        "responses_and_selections_replayed": True,
        "decision_denominator_exact": sum(item["selection_count"] for item in audited_cells) == 48,
        "claim_boundary_preserved": (
            report["claim_boundary"]["selection_only"] is True
            and report["claim_boundary"]["task_execution"] is False
            and report["claim_boundary"]["utility_verification"] is False
            and report["claim_boundary"]["full87_or_1305_cell_result"] is False
        ),
    }
    body: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "gpt56-selection-shadowing-pilot-raw-audit-v1",
        "pilot_id": PILOT_ID,
        "passed": all(checks.values()),
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "report_file_sha256": _sha256_bytes(report_path.read_bytes()),
        "report_sha256": report["report_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "cells": audited_cells,
        "claim_boundary": {
            "private_raw_provider_files_packaged": False,
            "provider_resolved_model_identity": report["claim_boundary"][
                "provider_resolved_model_identity"
            ],
            "provider_native_skill_invocation": False,
            "task_execution_or_utility_claim": False,
            "full87_or_statistical_claim": False,
        },
    }
    if not body["passed"]:
        raise SelectionPilotAuditError("selection pilot raw audit checks did not all pass")
    audit = {**body, "audit_sha256": _sha256_text(_canonical_json(body))}
    _write_new(
        output_path,
        (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        audit = audit_pilot(
            raw_root=args.raw_root,
            report_path=args.report,
            output_path=args.output,
        )
    except (OSError, SelectionPilotAuditError) as exc:
        parser.error(str(exc))
    print("Merlin GPT-5.6 selection pilot raw audit")
    print(f"checks={audit['checks_passed']}/{audit['checks_total']}")
    print(f"cells={len(audit['cells'])}")
    print(f"audit_sha256={audit['audit_sha256']}")
    print(f"safe_output={args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
