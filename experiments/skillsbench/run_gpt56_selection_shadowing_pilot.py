"""Run a bounded GPT-5.6 selection-only library-scale pilot.

This experiment measures skill selection, not task execution or utility.  Six
frozen single-reference tasks are presented against nested skill catalogs of
size 6, 16, 56, and 209.  Each arm is run in two separate provider turns with
different presentation orders.  Raw prompts/provider traces stay outside the
repository; the safe report contains hashes, selections, metrics, and explicit
claim boundaries only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parent
TASKS_ROOT = ROOT / "tasks"
SKILLS_ROOT = ROOT / "skills"
SKILLS_INDEX = ROOT / "skills-index.json"
LIBRARY_MANIFEST = ROOT / "library-scale-manifest.json"
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
MODEL_ID = "gpt-5.6-terra"
EFFORT = "medium"
PILOT_ID = "gpt56-selection-shadowing-pilot-v1"
BASE_SEED = 20260720
TRIAL_INDICES = (1, 2)
TASK_IDS = (
    "offer-letter-generator",
    "jax-computing-basics",
    "3d-scan-calc",
    "earthquake-plate-calculation",
    "dialogue-parser",
    "data-to-d3",
)
ARM_SIZES = (
    ("oracle-6", 6),
    ("plus-10", 16),
    ("plus-50", 56),
    ("full-209", 209),
)
MAX_DESCRIPTION_CHARS = 240
MAX_TASK_CHARS = 4_000
MAX_PROMPT_CHARS = 90_000
MAX_RAW_BYTES = 2_000_000
ALLOWED_ITEM_TYPES = frozenset({"agent_message", "reasoning"})
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})


class SelectionPilotError(ValueError):
    """Raised when a selection pilot violates its frozen evidence contract."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionPilotError(f"invalid JSON source: {path.name}") from exc
    if not isinstance(value, dict):
        raise SelectionPilotError(f"JSON source must be an object: {path.name}")
    return value


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise SelectionPilotError(f"refusing to overwrite pilot artifact: {path.name}") from exc


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        marker = text.find("\n---", 4)
        if marker >= 0:
            return text[marker + 4 :].strip()
    return text.strip()


def _frontmatter_description(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    marker = text.find("\n---", 4)
    if marker < 0:
        return ""
    frontmatter = text[4:marker]
    match = re.search(r"(?m)^description:\s*(.+?)\s*$", frontmatter)
    if not match:
        return ""
    value = match.group(1).strip()
    if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
        value = value[1:-1]
    return value.strip()


def _frontmatter_name(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    marker = text.find("\n---", 4)
    if marker < 0:
        return ""
    match = re.search(r"(?m)^name:\s*(.+?)\s*$", text[4:marker])
    if not match:
        return ""
    value = match.group(1).strip()
    if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
        value = value[1:-1]
    return value.strip()


def declared_skill_name(variant: str) -> str:
    path = SKILLS_ROOT / variant / "SKILL.md"
    if not path.is_file() or path.is_symlink():
        raise SelectionPilotError(f"skill variant has no regular SKILL.md: {variant}")
    name = _frontmatter_name(path.read_text(encoding="utf-8"))
    if not name:
        raise SelectionPilotError(f"skill variant has no declared frontmatter name: {variant}")
    return name


def _compact_text(value: str, limit: int) -> str:
    value = re.sub(r"```.*?```", " ", value, flags=re.DOTALL)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def _skill_summary(variant: str) -> tuple[str, str]:
    path = SKILLS_ROOT / variant / "SKILL.md"
    if not path.is_file() or path.is_symlink():
        raise SelectionPilotError(f"skill variant has no regular SKILL.md: {variant}")
    text = path.read_text(encoding="utf-8")
    description = _frontmatter_description(text)
    if not description:
        description = _strip_frontmatter(text)
    summary = _compact_text(description, MAX_DESCRIPTION_CHARS)
    if not summary:
        raise SelectionPilotError(f"skill variant has no usable summary: {variant}")
    return summary, _sha256_bytes(path.read_bytes())


def _task_instruction(task_id: str) -> tuple[str, str]:
    path = TASKS_ROOT / task_id / "task.md"
    if not path.is_file() or path.is_symlink():
        raise SelectionPilotError(f"task has no regular task.md: {task_id}")
    body = _strip_frontmatter(path.read_text(encoding="utf-8"))
    if not body or len(body) > MAX_TASK_CHARS:
        raise SelectionPilotError(f"task body is empty or above frozen size: {task_id}")
    return body, _sha256_bytes(path.read_bytes())


def _stable_order(values: list[str], *, namespace: str) -> list[str]:
    return sorted(values, key=lambda value: (_sha256_text(f"{namespace}:{value}"), value))


def _plan_body() -> dict[str, Any]:
    index = _read_json(SKILLS_INDEX)
    manifest = _read_json(LIBRARY_MANIFEST)
    skills = index.get("skills")
    contracts = manifest.get("task_contracts")
    if not isinstance(skills, list) or not isinstance(contracts, list):
        raise SelectionPilotError("canonical index or library manifest is malformed")
    variants = sorted(
        item.get("variant") for item in skills if isinstance(item, dict) and isinstance(item.get("variant"), str)
    )
    if len(variants) != len(set(variants)) or len(variants) != 209:
        raise SelectionPilotError("canonical skill pool must contain exactly 209 unique variants")
    by_task = {
        item.get("task_id"): item
        for item in contracts
        if isinstance(item, dict) and isinstance(item.get("task_id"), str)
    }
    tasks: list[dict[str, Any]] = []
    oracle_ids: list[str] = []
    for task_id in TASK_IDS:
        contract = by_task.get(task_id)
        if not isinstance(contract, dict):
            raise SelectionPilotError(f"frozen task missing from library manifest: {task_id}")
        references = contract.get("reference_skill_variants")
        if not isinstance(references, list) or len(references) != 1 or not isinstance(references[0], str):
            raise SelectionPilotError(f"pilot task must have exactly one reference skill: {task_id}")
        oracle_id = references[0]
        if oracle_id not in variants or oracle_id in oracle_ids:
            raise SelectionPilotError("pilot oracle skills must be distinct members of the 209 pool")
        instruction, instruction_sha = _task_instruction(task_id)
        if instruction_sha != contract.get("task_instruction_sha256"):
            raise SelectionPilotError(f"task instruction hash differs from library manifest: {task_id}")
        oracle_ids.append(oracle_id)
        tasks.append(
            {
                "task_id": task_id,
                "oracle_skill_id": oracle_id,
                "task_instruction_sha256": instruction_sha,
                "instruction_chars": len(instruction),
            }
        )
    summaries: dict[str, dict[str, Any]] = {}
    for variant in variants:
        summary, skill_sha = _skill_summary(variant)
        summaries[variant] = {
            "description": summary,
            "skill_md_sha256": skill_sha,
        }
    distractors = _stable_order(
        [variant for variant in variants if variant not in oracle_ids],
        namespace=f"{PILOT_ID}:membership:{BASE_SEED}",
    )
    arms: list[dict[str, Any]] = []
    for arm_id, size in ARM_SIZES:
        if size < len(oracle_ids) or size > len(variants):
            raise SelectionPilotError("pilot arm size is outside the canonical skill pool")
        members = tuple(oracle_ids + distractors[: size - len(oracle_ids)])
        if len(members) != size or len(set(members)) != size:
            raise SelectionPilotError("pilot arm membership is not exact and unique")
        arms.append(
            {
                "arm_id": arm_id,
                "library_size": size,
                "skill_ids": list(members),
                "membership_sha256": _sha256_text(_canonical_json(members)),
                "presentations": [
                    {
                        "trial_index": trial,
                        "skill_ids": _stable_order(
                            list(members),
                            namespace=f"{PILOT_ID}:presentation:{trial}:{arm_id}",
                        ),
                    }
                    for trial in TRIAL_INDICES
                ],
            }
        )
    return {
        "schema_version": 1,
        "pilot_id": PILOT_ID,
        "base_seed": BASE_SEED,
        "model_contract": {"requested_model_id": MODEL_ID, "effort": EFFORT},
        "trial_indices": list(TRIAL_INDICES),
        "task_count": len(tasks),
        "skill_pool_count": len(variants),
        "tasks": tasks,
        "arms": arms,
        "skill_records": summaries,
        "source_bindings": {
            "skills_index_sha256": _sha256_bytes(SKILLS_INDEX.read_bytes()),
            "library_scale_manifest_sha256": _sha256_bytes(LIBRARY_MANIFEST.read_bytes()),
        },
        "evaluation_contract": {
            "unit": "model skill selection from a presented catalog",
            "task_execution": False,
            "utility_verification": False,
            "provider_native_skill_invocation": False,
            "wrong_skill": "selected_skill_id is non-null and differs from frozen single reference",
            "abstention": "selected_skill_id is null for a task with a frozen single reference",
            "selection_shadowing": "wrong_skill or abstention",
            "headline_full87_claim_eligible": False,
            "presentation_trials_are_separate_provider_turns": True,
        },
    }


def build_plan() -> dict[str, Any]:
    body = _plan_body()
    return {**body, "plan_sha256": _sha256_text(_canonical_json(body))}


def _arm(plan: dict[str, Any], arm_id: str) -> dict[str, Any]:
    matches = [item for item in plan["arms"] if item["arm_id"] == arm_id]
    if len(matches) != 1:
        raise SelectionPilotError(f"unknown or duplicate arm: {arm_id}")
    return matches[0]


def _presentation(arm: dict[str, Any], trial_index: int) -> list[str]:
    matches = [
        item["skill_ids"]
        for item in arm["presentations"]
        if item["trial_index"] == trial_index
    ]
    if len(matches) != 1:
        raise SelectionPilotError("arm presentation trial is missing or duplicated")
    return matches[0]


def build_prompt(plan: dict[str, Any], *, arm_id: str, trial_index: int) -> str:
    arm = _arm(plan, arm_id)
    skill_ids = _presentation(arm, trial_index)
    task_records = []
    for task in plan["tasks"]:
        instruction, instruction_sha = _task_instruction(task["task_id"])
        if instruction_sha != task["task_instruction_sha256"]:
            raise SelectionPilotError("task bytes drifted after plan construction")
        task_records.append({"task_id": task["task_id"], "instruction": instruction})
    catalog = [
        {
            "skill_id": skill_id,
            "description": plan["skill_records"][skill_id]["description"],
        }
        for skill_id in skill_ids
    ]
    prompt = (
        "You are the selection component of a governed agent skill harness.\n"
        "For each task, select exactly one skill_id from the presented catalog that most "
        "directly helps complete the task. Return null only if no catalog skill is relevant.\n"
        "Do not call tools, inspect files, solve the tasks, or include explanations. "
        "Return only the required JSON. Treat each task independently.\n\n"
        f"CATALOG ({len(catalog)} skills, presentation order is intentional):\n"
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
        + "\n\nTASKS:\n"
        + json.dumps(task_records, ensure_ascii=False, separators=(",", ":"))
    )
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SelectionPilotError(f"pilot prompt exceeds {MAX_PROMPT_CHARS} characters")
    return prompt


def response_schema(plan: dict[str, Any], *, arm_id: str) -> dict[str, Any]:
    arm = _arm(plan, arm_id)
    task_ids = [task["task_id"] for task in plan["tasks"]]
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": ["selections"],
        "properties": {
            "selections": {
                "type": "array",
                "minItems": len(task_ids),
                "maxItems": len(task_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id", "selected_skill_id"],
                    "properties": {
                        "task_id": {"type": "string", "enum": task_ids},
                        "selected_skill_id": {
                            "anyOf": [
                                {"type": "string", "enum": arm["skill_ids"]},
                                {"type": "null"},
                            ]
                        },
                    },
                },
            }
        },
    }


def _item_types(raw_jsonl: str) -> tuple[str, ...]:
    result: list[str] = []
    for line_number, line in enumerate(raw_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionPilotError(f"provider JSONL line {line_number} is malformed") from exc
        if not isinstance(event, dict):
            raise SelectionPilotError("provider JSONL event is not an object")
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise SelectionPilotError("provider item event has no typed item")
        result.append(item["type"])
    unexpected = sorted(set(result) - ALLOWED_ITEM_TYPES)
    if unexpected:
        raise SelectionPilotError("provider used a tool or unsupported item: " + ", ".join(unexpected))
    return tuple(result)


def parse_response(
    raw_response: str, *, task_ids: tuple[str, ...], allowed_skill_ids: frozenset[str]
) -> tuple[dict[str, str | None], ...]:
    try:
        value = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise SelectionPilotError("provider selection response is not strict JSON") from exc
    if not isinstance(value, dict) or set(value) != {"selections"} or not isinstance(value["selections"], list):
        raise SelectionPilotError("provider selection response has an unsupported shape")
    if len(value["selections"]) != len(task_ids):
        raise SelectionPilotError("provider selection count differs from frozen task count")
    parsed: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in value["selections"]:
        if not isinstance(item, dict) or set(item) != {"task_id", "selected_skill_id"}:
            raise SelectionPilotError("provider selection item has an unsupported shape")
        task_id = item["task_id"]
        selected = item["selected_skill_id"]
        if not isinstance(task_id, str) or task_id not in task_ids or task_id in seen:
            raise SelectionPilotError("provider task selection is unknown or duplicated")
        if selected is not None and (not isinstance(selected, str) or selected not in allowed_skill_ids):
            raise SelectionPilotError("provider selected a skill outside the presented catalog")
        seen.add(task_id)
        parsed.append({"task_id": task_id, "selected_skill_id": selected})
    if seen != set(task_ids):
        raise SelectionPilotError("provider selection response omitted a task")
    by_id = {item["task_id"]: item for item in parsed}
    return tuple(by_id[task_id] for task_id in task_ids)


def _cli_version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"], text=True, capture_output=True, timeout=15, check=False
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise SelectionPilotError("unable to resolve Codex CLI version")
    return completed.stdout.strip()


def _command(
    *, executable: Path, workspace: Path, schema_path: Path, last_message: Path, model: str, effort: str
) -> list[str]:
    return [
        str(executable),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--output-schema",
        str(schema_path),
        "--cd",
        str(workspace),
        "--output-last-message",
        str(last_message),
        "-",
    ]


def run_cell(
    *,
    plan: dict[str, Any],
    arm_id: str,
    trial_index: int,
    raw_root: Path,
    executable: Path,
    model: str,
    effort: str,
    cli_version: str,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    arm = _arm(plan, arm_id)
    cell_id = f"{arm_id}__t{trial_index}"
    cell_root = raw_root / cell_id
    if cell_root.exists() or cell_root.is_symlink():
        raise SelectionPilotError(f"raw cell root already exists: {cell_id}")
    workspace = cell_root / "empty-workspace"
    workspace.mkdir(parents=True)
    prompt = build_prompt(plan, arm_id=arm_id, trial_index=trial_index)
    schema = response_schema(plan, arm_id=arm_id)
    schema_bytes = (json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    schema_path = cell_root / "response.schema.json"
    raw_path = cell_root / "provider.codex.jsonl"
    last_message = cell_root / "provider.last-message.json"
    stderr_path = cell_root / "provider.stderr.txt"
    _write_new(schema_path, schema_bytes)
    command = _command(
        executable=executable,
        workspace=workspace,
        schema_path=schema_path,
        last_message=last_message,
        model=model,
        effort=effort,
    )
    try:
        completed = runner(
            command,
            cwd=workspace,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        _write_new(raw_path, partial.encode("utf-8"))
        raise SelectionPilotError(f"provider selection cell timed out: {cell_id}") from exc
    raw_bytes = completed.stdout.encode("utf-8")
    if len(raw_bytes) > MAX_RAW_BYTES:
        raise SelectionPilotError(f"provider JSONL exceeds raw byte budget: {cell_id}")
    _write_new(raw_path, raw_bytes)
    if completed.returncode != 0:
        diagnostic = completed.stderr.replace(prompt, "<prompt-redacted>")[:20_000]
        _write_new(stderr_path, diagnostic.encode("utf-8"))
        raise SelectionPilotError(f"provider selection cell exited {completed.returncode}: {cell_id}")
    try:
        summary = parse_codex_exec_jsonl(completed.stdout)
    except CodexCliAdapterError as exc:
        raise SelectionPilotError(str(exc)) from exc
    item_types = _item_types(completed.stdout)
    if summary.reported_model_ids and model not in summary.reported_model_ids:
        raise SelectionPilotError("provider-reported model differs from requested model")
    response = summary.final_message
    if response is None and last_message.is_file():
        response = last_message.read_text(encoding="utf-8").strip()
    if not response:
        raise SelectionPilotError(f"provider selection cell returned no final response: {cell_id}")
    task_ids = tuple(task["task_id"] for task in plan["tasks"])
    parsed = parse_response(
        response.strip(),
        task_ids=task_ids,
        allowed_skill_ids=frozenset(arm["skill_ids"]),
    )
    oracle = {task["task_id"]: task["oracle_skill_id"] for task in plan["tasks"]}
    decisions = []
    for item in parsed:
        selected = item["selected_skill_id"]
        expected = oracle[item["task_id"]]
        outcome = "correct" if selected == expected else ("abstain" if selected is None else "wrong_skill")
        decisions.append({**item, "oracle_skill_id": expected, "outcome": outcome})
    return {
        "cell_id": cell_id,
        "arm_id": arm_id,
        "library_size": arm["library_size"],
        "trial_index": trial_index,
        "membership_sha256": arm["membership_sha256"],
        "presentation_sha256": _sha256_text(
            _canonical_json(_presentation(arm, trial_index))
        ),
        "prompt_sha256": _sha256_text(prompt),
        "schema_sha256": _sha256_bytes(schema_bytes),
        "raw_trace_pointer": f"{cell_id}/provider.codex.jsonl",
        "raw_trace_sha256": _sha256_bytes(raw_bytes),
        "response_sha256": _sha256_text(response.strip()),
        "requested_model_id": model,
        "provider_reported_model_ids": list(summary.reported_model_ids),
        "model_evidence_level": (
            "provider_reported" if summary.reported_model_ids else "requested_cli_contract_only"
        ),
        "effort": effort,
        "cli_version": cli_version,
        "event_count": summary.event_count,
        "item_types": list(item_types),
        "provider_tool_execution_observed": False,
        "decisions": decisions,
    }


def _metrics(cells: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, Any] = {}
    for arm_id, size in ARM_SIZES:
        selected = [cell for cell in cells if cell["arm_id"] == arm_id]
        decisions = [decision for cell in selected for decision in cell["decisions"]]
        counts = {
            name: sum(decision["outcome"] == name for decision in decisions)
            for name in ("correct", "wrong_skill", "abstain")
        }
        denominator = len(decisions)
        by_arm[arm_id] = {
            "library_size": size,
            "provider_turns": len(selected),
            "decision_count": denominator,
            "counts": counts,
            "selection_accuracy": counts["correct"] / denominator,
            "wrong_skill_rate": counts["wrong_skill"] / denominator,
            "abstention_rate": counts["abstain"] / denominator,
            "selection_shadowing_rate": (counts["wrong_skill"] + counts["abstain"]) / denominator,
        }
    baseline = by_arm["oracle-6"]["selection_accuracy"]
    for arm_id in by_arm:
        by_arm[arm_id]["accuracy_delta_vs_oracle_6"] = (
            by_arm[arm_id]["selection_accuracy"] - baseline
        )
    return {
        "arms": by_arm,
        "monotonic_nonincreasing_accuracy_observed": all(
            by_arm[current]["selection_accuracy"] >= by_arm[next_arm]["selection_accuracy"]
            for current, next_arm in zip(
                [item[0] for item in ARM_SIZES], [item[0] for item in ARM_SIZES][1:]
            )
        ),
    }


def validate_report(report: dict[str, Any], *, plan: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != 1 or report.get("pilot_id") != PILOT_ID:
        raise SelectionPilotError("pilot report schema or identity drifted")
    if report.get("plan_sha256") != plan["plan_sha256"]:
        raise SelectionPilotError("pilot report plan binding drifted")
    cells = report.get("cells")
    if not isinstance(cells, list) or len(cells) != len(ARM_SIZES) * len(TRIAL_INDICES):
        raise SelectionPilotError("pilot report cell denominator drifted")
    expected_cells = {
        f"{arm_id}__t{trial}" for arm_id, _ in ARM_SIZES for trial in TRIAL_INDICES
    }
    if {cell.get("cell_id") for cell in cells if isinstance(cell, dict)} != expected_cells:
        raise SelectionPilotError("pilot report cell IDs drifted")
    for cell in cells:
        if not isinstance(cell, dict):
            raise SelectionPilotError("pilot report cell is malformed")
        arm = _arm(plan, cell.get("arm_id"))
        if cell.get("library_size") != arm["library_size"]:
            raise SelectionPilotError("pilot cell library size drifted")
        if cell.get("provider_tool_execution_observed") is not False:
            raise SelectionPilotError("pilot cell contains provider tool execution")
        if set(cell.get("item_types", [])) - ALLOWED_ITEM_TYPES:
            raise SelectionPilotError("pilot cell item types drifted")
        decisions = cell.get("decisions")
        if not isinstance(decisions, list) or len(decisions) != len(TASK_IDS):
            raise SelectionPilotError("pilot cell decision denominator drifted")
        expected = {task["task_id"]: task["oracle_skill_id"] for task in plan["tasks"]}
        if {item.get("task_id") for item in decisions if isinstance(item, dict)} != set(expected):
            raise SelectionPilotError("pilot cell task decisions drifted")
        for item in decisions:
            selected = item.get("selected_skill_id")
            oracle = expected[item.get("task_id")]
            outcome = "correct" if selected == oracle else ("abstain" if selected is None else "wrong_skill")
            if selected is not None and selected not in arm["skill_ids"]:
                raise SelectionPilotError("pilot report selected a skill outside its arm")
            if item.get("oracle_skill_id") != oracle or item.get("outcome") != outcome:
                raise SelectionPilotError("pilot report decision outcome drifted")
        for field in ("prompt_sha256", "schema_sha256", "raw_trace_sha256", "response_sha256"):
            if not isinstance(cell.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", cell[field]):
                raise SelectionPilotError(f"pilot cell {field} is invalid")
    expected_metrics = _metrics(cells)
    if report.get("metrics") != expected_metrics:
        raise SelectionPilotError("pilot report metrics drifted")
    body = {
        key: value
        for key, value in report.items()
        if key not in {"report_sha256", "safe_audit"}
    }
    if report.get("report_sha256") != _sha256_text(_canonical_json(body)):
        raise SelectionPilotError("pilot report content hash drifted")
    return {
        "passed": True,
        "checks": 8,
        "cells": len(cells),
        "decisions": sum(len(cell["decisions"]) for cell in cells),
        "report_sha256": report["report_sha256"],
    }


def run_pilot(
    *,
    raw_root: Path,
    output_path: Path,
    executable: Path = DEFAULT_CODEX,
    model: str = MODEL_ID,
    effort: str = EFFORT,
) -> dict[str, Any]:
    if not MODEL_RE.fullmatch(model) or effort not in EFFORTS:
        raise SelectionPilotError("model or effort is outside the bounded contract")
    raw_root = raw_root.expanduser().resolve(strict=False)
    output_path = output_path.expanduser().resolve(strict=False)
    executable = executable.expanduser().resolve(strict=True)
    if raw_root.exists() or raw_root.is_symlink():
        raise SelectionPilotError("raw pilot root must be new-only")
    if raw_root.is_relative_to(REPO_ROOT):
        raise SelectionPilotError("raw provider pilot root must stay outside the repository")
    if output_path.exists() or output_path.is_symlink():
        raise SelectionPilotError("safe pilot output must be new-only")
    raw_root.mkdir(parents=True)
    plan = build_plan()
    cli_version = _cli_version(executable)
    cells = [
        run_cell(
            plan=plan,
            arm_id=arm_id,
            trial_index=trial,
            raw_root=raw_root,
            executable=executable,
            model=model,
            effort=effort,
            cli_version=cli_version,
        )
        for arm_id, _size in ARM_SIZES
        for trial in TRIAL_INDICES
    ]
    body: dict[str, Any] = {
        "schema_version": 1,
        "pilot_id": PILOT_ID,
        "plan_sha256": plan["plan_sha256"],
        "source_bindings": plan["source_bindings"],
        "model_contract": {
            "requested_model_id": model,
            "effort": effort,
            "cli_version": cli_version,
            "provider_turn_count": len(cells),
            "all_turns_no_provider_tool_execution": all(
                cell["provider_tool_execution_observed"] is False for cell in cells
            ),
        },
        "tasks": plan["tasks"],
        "arms": [
            {
                "arm_id": arm["arm_id"],
                "library_size": arm["library_size"],
                "membership_sha256": arm["membership_sha256"],
            }
            for arm in plan["arms"]
        ],
        "cells": cells,
        "metrics": _metrics(cells),
        "claim_boundary": {
            "actual_codex_provider_turns": True,
            "requested_gpt56_contract": model.startswith("gpt-5.6"),
            "provider_resolved_model_identity": all(
                bool(cell["provider_reported_model_ids"]) for cell in cells
            ),
            "selection_only": True,
            "task_execution": False,
            "utility_verification": False,
            "provider_native_skill_invocation": False,
            "full87_or_1305_cell_result": False,
            "statistical_significance_claim": False,
            "six_task_two_turn_pilot_only": True,
        },
    }
    report = {**body, "report_sha256": _sha256_text(_canonical_json(body))}
    audit = validate_report(report, plan=plan)
    report["safe_audit"] = audit
    # The audit is intentionally outside the content-addressed body so the
    # report hash remains self-verifiable without a recursive hash field.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_new(
        output_path,
        (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--effort", default=EFFORT)
    args = parser.parse_args(argv)
    try:
        report = run_pilot(
            raw_root=args.raw_root,
            output_path=args.output,
            executable=args.codex,
            model=args.model,
            effort=args.effort,
        )
    except (OSError, SelectionPilotError) as exc:
        parser.error(str(exc))
    print("Merlin GPT-5.6 selection-only shadowing pilot")
    print(f"provider_turns={report['model_contract']['provider_turn_count']}")
    for arm_id, metrics in report["metrics"]["arms"].items():
        print(
            f"{arm_id}: size={metrics['library_size']} "
            f"accuracy={metrics['selection_accuracy']:.1%} "
            f"wrong={metrics['wrong_skill_rate']:.1%} "
            f"abstain={metrics['abstention_rate']:.1%}"
        )
    print(f"safe_output={args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
