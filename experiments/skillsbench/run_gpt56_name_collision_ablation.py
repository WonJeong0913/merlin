"""Run a precommitted 56-skill same-name collision selection ablation.

This confirmatory follow-up is adaptive to the exploratory selection pilot: it
tests whether deterministic name-unique catalog provisioning changes exact
variant selection at a fixed library size.  It does not execute skills or claim
utility.  Raw and name-unique conditions each receive four separate requested
GPT-5.6 provider turns over the same six frozen tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from experiments.skillsbench.run_gpt56_selection_shadowing_pilot import (
    ALLOWED_ITEM_TYPES,
    DEFAULT_CODEX,
    EFFORT,
    EFFORTS,
    MODEL_ID,
    MODEL_RE,
    PILOT_ID,
    REPO_ROOT,
    TASK_IDS,
    SelectionPilotError,
    _arm,
    _canonical_json,
    _cli_version,
    _item_types,
    _presentation,
    _sha256_bytes,
    _sha256_text,
    _stable_order,
    build_plan as build_exploratory_plan,
    build_prompt,
    declared_skill_name,
    parse_response,
    response_schema,
    run_cell,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl


ROOT = Path(__file__).resolve().parent
EXPLORATORY_REPORT = ROOT / "results" / "gpt56-selection-shadowing-pilot-v1.json"
ABLATION_ID = "gpt56-name-collision-ablation-v1"
TRIAL_INDICES = (101, 102, 103, 104)
CONDITIONS = ("raw-56", "name-unique-56")


class NameCollisionAblationError(ValueError):
    """Raised when the confirmatory collision ablation contract drifts."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NameCollisionAblationError(f"invalid ablation JSON input: {path.name}") from exc
    if not isinstance(value, dict):
        raise NameCollisionAblationError("ablation JSON input must be an object")
    return value


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise NameCollisionAblationError(f"refusing to overwrite ablation artifact: {path.name}") from exc


def _preferred_variant(variant: str, declared_name: str) -> tuple[int, int, str]:
    """Prefer exact name IDs, then unversioned IDs, then lexical IDs."""

    return (
        0 if variant == declared_name else 1,
        0 if "@" not in variant else 1,
        variant,
    )


def _name_collision_summary(skill_ids: list[str]) -> dict[str, Any]:
    names = [declared_skill_name(skill_id) for skill_id in skill_ids]
    counts = Counter(names)
    collisions = {
        name: sorted(
            skill_id
            for skill_id in skill_ids
            if declared_skill_name(skill_id) == name
        )
        for name, count in counts.items()
        if count > 1
    }
    return {
        "declared_name_count": len(counts),
        "duplicate_name_count": len(collisions),
        "duplicate_variant_count": sum(len(ids) - 1 for ids in collisions.values()),
        "collision_groups": collisions,
    }


def build_plan() -> dict[str, Any]:
    base = build_exploratory_plan()
    exploratory = _read_json(EXPLORATORY_REPORT)
    if exploratory.get("pilot_id") != PILOT_ID:
        raise NameCollisionAblationError("exploratory pilot identity drifted")
    observed = [
        decision
        for cell in exploratory.get("cells", [])
        if cell.get("cell_id") == "plus-50__t2"
        for decision in cell.get("decisions", [])
        if decision.get("task_id") == "offer-letter-generator"
    ]
    if observed != [
        {
            "oracle_skill_id": "docx",
            "outcome": "wrong_skill",
            "selected_skill_id": "docx@d3cfe519dca2",
            "task_id": "offer-letter-generator",
        }
    ]:
        raise NameCollisionAblationError("adaptive exploratory observation drifted")
    raw_members = list(_arm(base, "plus-50")["skill_ids"])
    full_members = list(_arm(base, "full-209")["skill_ids"])
    oracle_ids = [task["oracle_skill_id"] for task in base["tasks"]]
    names = {skill_id: declared_skill_name(skill_id) for skill_id in full_members}
    grouped: dict[str, list[str]] = {}
    for skill_id in full_members:
        grouped.setdefault(names[skill_id], []).append(skill_id)
    canonical = {
        name: min(ids, key=lambda skill_id: _preferred_variant(skill_id, name))
        for name, ids in grouped.items()
    }
    unique_members = list(oracle_ids)
    used_names = {names[skill_id] for skill_id in oracle_ids}
    for skill_id in full_members:
        name = names[skill_id]
        if name in used_names or canonical[name] != skill_id:
            continue
        unique_members.append(skill_id)
        used_names.add(name)
        if len(unique_members) == 56:
            break
    if len(raw_members) != 56 or len(unique_members) != 56:
        raise NameCollisionAblationError("ablation catalogs must both contain exactly 56 variants")
    if not set(oracle_ids).issubset(unique_members):
        raise NameCollisionAblationError("name-unique catalog dropped a frozen reference skill")
    raw_collisions = _name_collision_summary(raw_members)
    unique_collisions = _name_collision_summary(unique_members)
    if raw_collisions["duplicate_name_count"] < 1:
        raise NameCollisionAblationError("raw 56-skill catalog has no declared-name collision")
    if unique_collisions["duplicate_name_count"] != 0:
        raise NameCollisionAblationError("name-unique catalog still contains a collision")

    arms = []
    for condition_id, members in (
        ("raw-56", raw_members),
        ("name-unique-56", unique_members),
    ):
        arms.append(
            {
                "arm_id": condition_id,
                "library_size": 56,
                "skill_ids": members,
                "membership_sha256": _sha256_text(_canonical_json(members)),
                "name_collision_summary": (
                    raw_collisions if condition_id == "raw-56" else unique_collisions
                ),
                "presentations": [
                    {
                        "trial_index": trial,
                        "skill_ids": _stable_order(
                            list(members),
                            namespace=f"{ABLATION_ID}:presentation:{condition_id}:{trial}",
                        ),
                    }
                    for trial in TRIAL_INDICES
                ],
            }
        )
    body: dict[str, Any] = {
        "schema_version": 1,
        "ablation_id": ABLATION_ID,
        "adaptive_source": {
            "exploratory_pilot_id": exploratory["pilot_id"],
            "exploratory_report_file_sha256": _sha256_bytes(EXPLORATORY_REPORT.read_bytes()),
            "observed_cell_id": "plus-50__t2",
            "observed_mismatch_task_id": observed[0]["task_id"],
            "observed_reference_skill_id": observed[0]["oracle_skill_id"],
            "observed_selected_skill_id": observed[0]["selected_skill_id"],
        },
        "trial_indices": list(TRIAL_INDICES),
        "tasks": base["tasks"],
        "skill_records": base["skill_records"],
        "source_bindings": base["source_bindings"],
        "arms": arms,
        "hypotheses": {
            "primary": "name-unique-56 exact-reference error rate is no greater than raw-56",
            "secondary": "declared-name accuracy is at least exact-variant accuracy",
            "confirmatory_scope": "fixed six tasks, fixed size 56, four fresh provider turns per condition",
        },
        "provisioning_policy": {
            "uses_task_oracle_to_choose_between_same-name_variants": False,
            "canonical_preference_order": [
                "variant ID exactly equals declared frontmatter name",
                "unversioned variant ID",
                "lexical variant ID",
            ],
            "removed_names_are_replaced_to_hold_library_size_constant": True,
            "source_library_mutated": False,
        },
        "claim_boundary": {
            "adaptive_followup_to_exploratory_result": True,
            "selection_only": True,
            "task_execution": False,
            "utility_verification": False,
            "provider_native_skill_invocation": False,
            "full87_result": False,
            "population_generalization": False,
        },
    }
    return {**body, "plan_sha256": _sha256_text(_canonical_json(body))}


def _augment_decisions(plan: dict[str, Any], cell: dict[str, Any]) -> None:
    oracle = {task["task_id"]: task["oracle_skill_id"] for task in plan["tasks"]}
    for decision in cell["decisions"]:
        selected = decision["selected_skill_id"]
        reference = oracle[decision["task_id"]]
        reference_name = declared_skill_name(reference)
        selected_name = None if selected is None else declared_skill_name(selected)
        decision["oracle_declared_name"] = reference_name
        decision["selected_declared_name"] = selected_name
        decision["declared_name_match"] = selected_name == reference_name


def _metrics(cells: list[dict[str, Any]]) -> dict[str, Any]:
    conditions: dict[str, Any] = {}
    for condition_id in CONDITIONS:
        selected = [cell for cell in cells if cell["arm_id"] == condition_id]
        decisions = [item for cell in selected for item in cell["decisions"]]
        exact = sum(item["outcome"] == "correct" for item in decisions)
        wrong = sum(item["outcome"] == "wrong_skill" for item in decisions)
        abstain = sum(item["outcome"] == "abstain" for item in decisions)
        name_match = sum(item["declared_name_match"] for item in decisions)
        denominator = len(decisions)
        conditions[condition_id] = {
            "library_size": 56,
            "provider_turns": len(selected),
            "decision_count": denominator,
            "exact_correct": exact,
            "wrong_variant": wrong,
            "abstain": abstain,
            "declared_name_correct": name_match,
            "exact_accuracy": exact / denominator,
            "exact_error_rate": (wrong + abstain) / denominator,
            "declared_name_accuracy": name_match / denominator,
        }
    raw = conditions["raw-56"]
    unique = conditions["name-unique-56"]
    return {
        "conditions": conditions,
        "exact_error_rate_delta_name_unique_minus_raw": (
            unique["exact_error_rate"] - raw["exact_error_rate"]
        ),
        "primary_hypothesis_supported_in_sample": (
            unique["exact_error_rate"] <= raw["exact_error_rate"]
        ),
        "strict_exact_improvement_observed": (
            unique["exact_error_rate"] < raw["exact_error_rate"]
        ),
    }


def validate_report(report: dict[str, Any], *, plan: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != 1 or report.get("ablation_id") != ABLATION_ID:
        raise NameCollisionAblationError("ablation report schema or identity drifted")
    if report.get("plan_sha256") != plan["plan_sha256"]:
        raise NameCollisionAblationError("ablation report plan binding drifted")
    cells = report.get("cells")
    if not isinstance(cells, list) or len(cells) != 8:
        raise NameCollisionAblationError("ablation report must contain exactly eight cells")
    expected_ids = {
        f"{condition}__t{trial}" for condition in CONDITIONS for trial in TRIAL_INDICES
    }
    if {cell.get("cell_id") for cell in cells if isinstance(cell, dict)} != expected_ids:
        raise NameCollisionAblationError("ablation cell IDs drifted")
    oracle = {task["task_id"]: task["oracle_skill_id"] for task in plan["tasks"]}
    for cell in cells:
        arm = _arm(plan, cell.get("arm_id"))
        trial_index = cell.get("trial_index")
        if (
            cell.get("library_size") != 56
            or trial_index not in TRIAL_INDICES
            or cell.get("membership_sha256") != arm["membership_sha256"]
            or cell.get("presentation_sha256")
            != _sha256_text(_canonical_json(_presentation(arm, trial_index)))
            or cell.get("provider_tool_execution_observed") is not False
        ):
            raise NameCollisionAblationError("ablation cell boundary drifted")
        if set(cell.get("item_types", [])) - ALLOWED_ITEM_TYPES:
            raise NameCollisionAblationError("ablation provider item type drifted")
        decisions = cell.get("decisions")
        if not isinstance(decisions, list) or len(decisions) != len(TASK_IDS):
            raise NameCollisionAblationError("ablation decision denominator drifted")
        for decision in decisions:
            task_id = decision.get("task_id")
            selected = decision.get("selected_skill_id")
            if task_id not in oracle or (selected is not None and selected not in arm["skill_ids"]):
                raise NameCollisionAblationError("ablation decision left its frozen contract")
            reference = oracle[task_id]
            outcome = "correct" if selected == reference else ("abstain" if selected is None else "wrong_skill")
            reference_name = declared_skill_name(reference)
            selected_name = None if selected is None else declared_skill_name(selected)
            if not (
                decision.get("oracle_skill_id") == reference
                and decision.get("outcome") == outcome
                and decision.get("oracle_declared_name") == reference_name
                and decision.get("selected_declared_name") == selected_name
                and decision.get("declared_name_match") == (selected_name == reference_name)
            ):
                raise NameCollisionAblationError("ablation decision derivation drifted")
        for field in ("prompt_sha256", "schema_sha256", "raw_trace_sha256", "response_sha256"):
            if not isinstance(cell.get(field), str) or re.fullmatch(r"[0-9a-f]{64}", cell[field]) is None:
                raise NameCollisionAblationError(f"ablation cell {field} is invalid")
    if report.get("metrics") != _metrics(cells):
        raise NameCollisionAblationError("ablation metrics drifted")
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if report.get("report_sha256") != _sha256_text(_canonical_json(body)):
        raise NameCollisionAblationError("ablation report hash drifted")
    return {"passed": True, "checks": 7, "cells": 8, "decisions": 48}


def audit_raw(*, report: dict[str, Any], plan: dict[str, Any], raw_root: Path) -> dict[str, Any]:
    validate_report(report, plan=plan)
    expected_dirs = {cell["cell_id"] for cell in report["cells"]}
    actual_dirs = {path.name for path in raw_root.iterdir() if path.is_dir()}
    if actual_dirs != expected_dirs:
        raise NameCollisionAblationError("ablation raw cell directory set drifted")
    audited = []
    for cell in report["cells"]:
        arm = _arm(plan, cell["arm_id"])
        cell_root = raw_root / cell["cell_id"]
        raw_path = cell_root / "provider.codex.jsonl"
        schema_path = cell_root / "response.schema.json"
        if not raw_path.is_file() or raw_path.is_symlink() or not schema_path.is_file() or schema_path.is_symlink():
            raise NameCollisionAblationError("ablation raw trace or schema is missing/linked")
        raw_bytes = raw_path.read_bytes()
        if _sha256_bytes(raw_bytes) != cell["raw_trace_sha256"]:
            raise NameCollisionAblationError("ablation raw trace hash drifted")
        expected_schema = (
            json.dumps(response_schema(plan, arm_id=cell["arm_id"]), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if schema_path.read_bytes() != expected_schema or _sha256_bytes(expected_schema) != cell["schema_sha256"]:
            raise NameCollisionAblationError("ablation response schema drifted")
        prompt = build_prompt(plan, arm_id=cell["arm_id"], trial_index=cell["trial_index"])
        if _sha256_text(prompt) != cell["prompt_sha256"]:
            raise NameCollisionAblationError("ablation prompt reconstruction drifted")
        try:
            raw_text = raw_bytes.decode("utf-8")
            summary = parse_codex_exec_jsonl(raw_text)
            item_types = _item_types(raw_text)
        except (UnicodeDecodeError, CodexCliAdapterError, SelectionPilotError) as exc:
            raise NameCollisionAblationError("ablation provider trace parse failed") from exc
        response = summary.final_message
        last = cell_root / "provider.last-message.json"
        if response is None and last.is_file():
            response = last.read_text(encoding="utf-8").strip()
        if not response or _sha256_text(response.strip()) != cell["response_sha256"]:
            raise NameCollisionAblationError("ablation response hash drifted")
        parsed = parse_response(
            response.strip(),
            task_ids=TASK_IDS,
            allowed_skill_ids=frozenset(arm["skill_ids"]),
        )
        recorded = [
            {"task_id": item["task_id"], "selected_skill_id": item["selected_skill_id"]}
            for item in cell["decisions"]
        ]
        if list(parsed) != recorded or list(item_types) != cell["item_types"]:
            raise NameCollisionAblationError("ablation raw selections or item types drifted")
        audited.append(
            {
                "cell_id": cell["cell_id"],
                "raw_trace_sha256": cell["raw_trace_sha256"],
                "selection_count": len(parsed),
                "provider_tool_execution_observed": False,
            }
        )
    checks = {
        "report_revalidated": True,
        "raw_cell_set_exact": len(audited) == 8,
        "raw_trace_hashes_exact": True,
        "schemas_exact": True,
        "prompts_reconstructed": True,
        "provider_no_tool": True,
        "responses_replayed": True,
        "selections_exact": True,
        "decision_denominator_exact": sum(item["selection_count"] for item in audited) == 48,
        "adaptive_claim_boundary_preserved": report["claim_boundary"]["adaptive_followup"] is True,
    }
    body = {
        "schema_version": 1,
        "audit_id": "gpt56-name-collision-ablation-raw-audit-v1",
        "ablation_id": ABLATION_ID,
        "passed": all(checks.values()),
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "report_sha256": report["report_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "cells": audited,
        "claim_boundary": {
            "selection_only": True,
            "task_execution_or_utility": False,
            "provider_native_skill_invocation": False,
            "full87_or_population_claim": False,
        },
    }
    return {**body, "audit_sha256": _sha256_text(_canonical_json(body))}


def run_ablation(
    *,
    raw_root: Path,
    output_path: Path,
    audit_output_path: Path,
    executable: Path = DEFAULT_CODEX,
    model: str = MODEL_ID,
    effort: str = EFFORT,
) -> dict[str, Any]:
    raw_root = raw_root.expanduser().resolve(strict=False)
    output_path = output_path.expanduser().resolve(strict=False)
    audit_output_path = audit_output_path.expanduser().resolve(strict=False)
    executable = executable.expanduser().resolve(strict=True)
    if not MODEL_RE.fullmatch(model) or effort not in EFFORTS:
        raise NameCollisionAblationError("requested model or reasoning effort is invalid")
    if raw_root.exists() or raw_root.is_symlink() or raw_root.is_relative_to(REPO_ROOT):
        raise NameCollisionAblationError("ablation raw root must be new and outside the repository")
    if output_path.exists() or audit_output_path.exists():
        raise NameCollisionAblationError("ablation safe outputs must be new-only")
    raw_root.mkdir(parents=True)
    plan = build_plan()
    cli_version = _cli_version(executable)
    cells = []
    for condition in CONDITIONS:
        for trial in TRIAL_INDICES:
            cell = run_cell(
                plan=plan,
                arm_id=condition,
                trial_index=trial,
                raw_root=raw_root,
                executable=executable,
                model=model,
                effort=effort,
                cli_version=cli_version,
            )
            _augment_decisions(plan, cell)
            cells.append(cell)
    body: dict[str, Any] = {
        "schema_version": 1,
        "ablation_id": ABLATION_ID,
        "plan_sha256": plan["plan_sha256"],
        "adaptive_source": plan["adaptive_source"],
        "hypotheses": plan["hypotheses"],
        "provisioning_policy": plan["provisioning_policy"],
        "arms": [
            {
                "arm_id": arm["arm_id"],
                "library_size": arm["library_size"],
                "membership_sha256": arm["membership_sha256"],
                "name_collision_summary": arm["name_collision_summary"],
            }
            for arm in plan["arms"]
        ],
        "model_contract": {
            "requested_model_id": model,
            "effort": effort,
            "cli_version": cli_version,
            "provider_turns": len(cells),
            "provider_resolved_model_identity": all(
                bool(cell["provider_reported_model_ids"]) for cell in cells
            ),
            "provider_tool_execution_observed": False,
        },
        "cells": cells,
        "metrics": _metrics(cells),
        "claim_boundary": {
            "adaptive_followup": True,
            "actual_codex_provider_turns": True,
            "selection_only": True,
            "task_execution": False,
            "utility_verification": False,
            "provider_native_skill_invocation": False,
            "full87_result": False,
            "statistical_significance": False,
            "population_generalization": False,
        },
    }
    report = {**body, "report_sha256": _sha256_text(_canonical_json(body))}
    validate_report(report, plan=plan)
    audit = audit_raw(report=report, plan=plan, raw_root=raw_root)
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    audit["report_file_sha256"] = _sha256_bytes(report_bytes)
    audit_body = {key: value for key, value in audit.items() if key != "audit_sha256"}
    audit["audit_sha256"] = _sha256_text(_canonical_json(audit_body))
    _write_new(output_path, report_bytes)
    _write_new(
        audit_output_path,
        (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--effort", default=EFFORT)
    args = parser.parse_args(argv)
    try:
        report = run_ablation(
            raw_root=args.raw_root,
            output_path=args.output,
            audit_output_path=args.audit_output,
            executable=args.codex,
            model=args.model,
            effort=args.effort,
        )
    except (OSError, SelectionPilotError, NameCollisionAblationError) as exc:
        parser.error(str(exc))
    print("Merlin GPT-5.6 same-name collision ablation")
    for condition, metrics in report["metrics"]["conditions"].items():
        print(
            f"{condition}: exact={metrics['exact_correct']}/{metrics['decision_count']} "
            f"name={metrics['declared_name_correct']}/{metrics['decision_count']}"
        )
    print(
        "strict_improvement="
        + str(report["metrics"]["strict_exact_improvement_observed"]).lower()
    )
    print(f"safe_output={args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
