"""Run two bounded account-auth personal-workload pilot pairs.

The pilot exercises matched arm ordering, real Codex CLI turns, and the live
HarnessX PreToolUse/PostToolUse boundary.  It deliberately does not append to
the longitudinal ledger because Codex JSONL does not expose provider-native
Merlin skill invocation evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiments.mvp.run_chat import detect_codex_runtime
from src.merlin_harness.codex_chat import (
    CodexChatBackend,
    HarnessXLiveHookConfig,
)
from src.merlin_harness.harnessx_live_hook import (
    load_and_validate_live_hook_audit,
)
from src.merlin_harness.personal_workload_campaign import (
    PERSONAL_WORKLOAD_50_TASKS,
    PERSONAL_WORKLOAD_MANIFEST_SHA256,
    PERSONAL_WORKLOAD_SCHEDULE_SHA256,
    personal_workload_schedule_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1"
    / "pilot-account-auth-2pairs-v1"
)
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)authorization\s*[:=]"),
    re.compile(r"(?i)(openai|api)[_-]?key\s*[:=]\s*\S+"),
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _write_new_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")


@dataclass(frozen=True, slots=True)
class PilotTask:
    task_id: str
    label: str
    fixture_kind: str
    exact_command: str
    success_markers: tuple[str, ...]


PILOT_TASKS = (
    PilotTask(
        task_id="pw-ke-09",
        label="bounded-campaign-contract-replay",
        fixture_kind="campaign_projection",
        exact_command="PYTHONDONTWRITEBYTECODE=1 python3 verify_task.py",
        success_markers=(
            '"valid": true',
            '"task_count": 50',
            '"pair_count": 100',
            '"observation_count": 0',
        ),
    ),
    PilotTask(
        task_id="pw-ke-08",
        label="bounded-evolution-ledger-replay",
        fixture_kind="evolution_projection",
        exact_command="PYTHONDONTWRITEBYTECODE=1 python3 verify_task.py",
        success_markers=(
            '"valid": true',
            '"record_count": 12',
            '"promotion_count": 1',
            '"rollback_count": 1',
        ),
    ),
)


def _schedule_row(task_id: str) -> dict[str, Any]:
    for row in personal_workload_schedule_payload()["pairs"]:
        if row["task_id"] == task_id and row["repetition"] == 1:
            return row
    raise ValueError(f"pilot task is outside phase-1 schedule: {task_id}")


def _task_contract(task_id: str) -> dict[str, Any]:
    for task in PERSONAL_WORKLOAD_50_TASKS:
        if task.task_id == task_id:
            return task.to_dict()
    raise ValueError(f"unknown personal workload task: {task_id}")


def _parse_raw_trace(
    raw_path: Path,
    *,
    expected_command: str,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        raw_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"malformed provider JSONL at line {line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise ValueError("provider event must be an object")
        events.append(event)
    completed_commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    usage: dict[str, Any] = {}
    for event in events:
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), dict
        ):
            usage = dict(event["usage"])
    expected_observed = any(
        item.get("exit_code") == 0
        and expected_command in str(item.get("command", ""))
        for item in completed_commands
    )
    write_like = any(
        re.search(
            r"(?i)(?:\btouch\b|\brm\b|\bmv\b|\bcp\b|apply_patch|"
            r"\btruncate\b|(?:^|[;&|])\s*[^ ]+\s*>)",
            str(item.get("command", "")),
        )
        for item in completed_commands
    )
    aggregated_output = "\n".join(
        str(item.get("aggregated_output", ""))
        for item in completed_commands
    )
    return {
        "event_count": len(events),
        "event_types": [event.get("type") for event in events],
        "completed_command_count": len(completed_commands),
        "expected_command_observed": expected_observed,
        "write_like_command_observed": write_like,
        "completed_command_exit_codes": [
            item.get("exit_code") for item in completed_commands
        ],
        "aggregated_output": aggregated_output,
        "usage": usage,
    }


def _scan_secret_like_text(paths: tuple[Path, ...]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
                hits.append(f"{path.name}:{line_number}")
    return hits


def _campaign_fixture() -> tuple[dict[str, Any], str]:
    schedule = personal_workload_schedule_payload()
    projection = {
        "schema_version": "public-safe-campaign-projection-v1",
        "manifest_sha256": PERSONAL_WORKLOAD_MANIFEST_SHA256,
        "schedule_sha256": PERSONAL_WORKLOAD_SCHEDULE_SHA256,
        "task_count": 50,
        "pair_count": 100,
        "observation_count": 0,
        "g_over_s": None,
        "level_7_achieved": False,
        "pairs": [
            {
                "pair_id": row["pair_id"],
                "task_id": row["task_id"],
                "repetition": row["repetition"],
                "arm_order": row["arm_order"],
            }
            for row in schedule["pairs"]
        ],
    }
    verifier = """\
import json
from pathlib import Path

payload = json.loads(Path("campaign-projection.json").read_text(encoding="utf-8"))
pairs = payload["pairs"]
checks = {
    "schema": payload["schema_version"] == "public-safe-campaign-projection-v1",
    "task_count": payload["task_count"] == 50,
    "pair_count": payload["pair_count"] == 100 and len(pairs) == 100,
    "observation_count": payload["observation_count"] == 0,
    "g_over_s": payload["g_over_s"] is None,
    "level_7": payload["level_7_achieved"] is False,
    "unique_pairs": len({row["pair_id"] for row in pairs}) == 100,
    "two_repeats": all(
        sum(row["task_id"] == task_id for row in pairs) == 2
        for task_id in {row["task_id"] for row in pairs}
    ),
    "arm_order": all(
        row["arm_order"] in (
            ["baseline", "managed"],
            ["managed", "baseline"],
        )
        for row in pairs
    ),
}
result = {
    "valid": all(checks.values()),
    "task_count": payload["task_count"],
    "pair_count": payload["pair_count"],
    "observation_count": payload["observation_count"],
    "checks": checks,
}
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result["valid"] else 2)
"""
    return projection, verifier


def _evolution_fixture() -> tuple[list[dict[str, Any]], str]:
    kinds = (
        "promote",
        "repair",
        "repair",
        "repair",
        "harness-update",
        "repair",
        "repair",
        "rollback",
        "repair",
        "repair",
        "harness-update",
        "repair",
    )
    rows: list[dict[str, Any]] = []
    previous = "0" * 64
    for sequence, kind in enumerate(kinds, start=1):
        body = {
            "sequence": sequence,
            "previous_record_sha256": previous,
            "action_kind": kind,
            "evidence_sha256": _sha256_bytes(
                f"public-safe-evidence-{sequence}".encode("utf-8")
            ),
        }
        record_hash = _sha256_json(body)
        rows.append({**body, "record_sha256": record_hash})
        previous = record_hash
    verifier = """\
import hashlib
import json
from pathlib import Path

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

rows = [
    json.loads(line)
    for line in Path("evolution-projection.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
previous = "0" * 64
chain_valid = True
for expected_sequence, row in enumerate(rows, start=1):
    body = {
        "sequence": row["sequence"],
        "previous_record_sha256": row["previous_record_sha256"],
        "action_kind": row["action_kind"],
        "evidence_sha256": row["evidence_sha256"],
    }
    expected_hash = hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
    if (
        row["sequence"] != expected_sequence
        or row["previous_record_sha256"] != previous
        or row["record_sha256"] != expected_hash
    ):
        chain_valid = False
    previous = row["record_sha256"]
kinds = [row["action_kind"] for row in rows]
result = {
    "valid": chain_valid and len(rows) == 12,
    "record_count": len(rows),
    "promotion_count": kinds.count("promote"),
    "rollback_count": kinds.count("rollback"),
    "chain_valid": chain_valid,
}
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result["valid"] else 2)
"""
    return rows, verifier


def _prepare_workspace(workspace: Path, task: PilotTask) -> None:
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "PUBLIC_SAFE_BOUNDED_FIXTURE.txt").write_text(
        "This workspace contains only generated hashes, counts, IDs, and a "
        "standalone verifier. It contains no private project source, user "
        "content, credential, or raw provider transcript.\n",
        encoding="utf-8",
    )
    if task.fixture_kind == "campaign_projection":
        projection, verifier = _campaign_fixture()
        _write_new_json(workspace / "campaign-projection.json", projection)
    elif task.fixture_kind == "evolution_projection":
        rows, verifier = _evolution_fixture()
        with (workspace / "evolution-projection.jsonl").open(
            "x", encoding="utf-8"
        ) as handle:
            for row in rows:
                handle.write(_canonical_json(row) + "\n")
    else:
        raise ValueError(f"unknown pilot fixture kind: {task.fixture_kind}")
    (workspace / "verify_task.py").write_text(verifier, encoding="utf-8")


def _input_snapshot(workspace: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or ".merlin" in path.parts:
            continue
        relative = path.relative_to(workspace).as_posix()
        snapshot[relative] = _sha256_bytes(path.read_bytes())
    return snapshot


def _prompt(task: PilotTask, arm: str) -> str:
    base = (
        "You are executing a frozen, read-only verification task for a matched "
        "account-auth pilot. Use the shell exactly once to run this exact "
        f"command:\n\n{task.exact_command}\n\n"
        "Do not inspect unrelated files, do not modify files, and do not use "
        "apply_patch. After the command finishes, return a compact JSON object "
        "with keys task_id, verifier_passed, observed_facts, and limitations. "
        "Set verifier_passed only from the command output."
    )
    if arm == "baseline":
        return base
    skill = (
        "\n\nMerlin provisioned the following minimal skill contract as "
        "prompt context:\n"
        "SKILL_ID=verification-replay-v1\n"
        "CONTRACT: run only the frozen verifier command; treat exit zero as "
        "necessary but not sufficient; report evidence boundaries; never turn "
        "prompt exposure into a provider-native invocation claim.\n"
        "This context is exposure-level evidence only."
    )
    return base + skill


def _run_arm(
    *,
    task: PilotTask,
    arm: str,
    output_root: Path,
    executable: Path,
    cli_version: str,
    model_id: str,
    effort: str,
    timeout_s: float,
) -> dict[str, Any]:
    workspace = output_root / "workspaces" / task.task_id / arm
    _prepare_workspace(workspace, task)
    input_snapshot_before = _input_snapshot(workspace)
    trace_root = workspace / ".merlin" / "chat" / "turn-1"
    live_config = (
        HarnessXLiveHookConfig(
            project_root=REPO_ROOT,
            python_executable=Path(sys.executable),
            allowed_commands=(task.exact_command,),
            denied_tools=("apply_patch",),
        )
        if arm == "managed"
        else None
    )
    backend = CodexChatBackend(
        executable=executable,
        cli_version=cli_version,
        workspace=workspace,
        trace_root=trace_root,
        model_id=model_id,
        effort=effort,
        timeout_s=timeout_s,
        live_hook_config=live_config,
    )
    started = time.monotonic()
    result = backend.run_turn(
        prompt=_prompt(task, arm),
        turn_number=1,
        thread_id=None,
    )
    latency_s = time.monotonic() - started
    input_snapshot_after = _input_snapshot(workspace)
    raw_path = trace_root / result.raw_trace_pointer
    parsed = _parse_raw_trace(
        raw_path,
        expected_command=task.exact_command,
    )
    answer_markers_passed = all(
        marker in (parsed["aggregated_output"] + "\n" + result.answer)
        for marker in task.success_markers
    )
    audit_records: list[dict[str, Any]] = []
    if backend.live_hook_audit_path is not None:
        audit_records = list(
            load_and_validate_live_hook_audit(backend.live_hook_audit_path)
        )
    pre_allow_count = sum(
        record.get("phase") == "pre_tool_use"
        and record.get("decision") == "allow"
        for record in audit_records
    )
    post_count = sum(
        record.get("phase") == "post_tool_use"
        for record in audit_records
    )
    hook_passed = (
        pre_allow_count >= 1 and post_count >= 1
        if arm == "managed"
        else not audit_records
    )
    verifier_passed = (
        parsed["expected_command_observed"]
        and not parsed["write_like_command_observed"]
        and all(code == 0 for code in parsed["completed_command_exit_codes"])
        and answer_markers_passed
        and hook_passed
        and input_snapshot_after == input_snapshot_before
    )
    secret_hits = _scan_secret_like_text(
        (
            raw_path,
            trace_root / "turn-0001.last-message.txt",
            trace_root / "turn-0001.stderr.txt",
        )
    )
    if secret_hits:
        raise RuntimeError(
            "secret-like content detected in pilot artifacts: "
            + ", ".join(secret_hits)
        )
    usage = parsed["usage"]
    return {
        "arm": arm,
        "provider": result.metadata["provider"],
        "cli_version": cli_version,
        "requested_model_id": model_id,
        "requested_effort": effort,
        "provider_reported_model_ids": result.metadata[
            "provider_reported_model_ids"
        ],
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "execution_turns": 1,
        "latency_s": latency_s,
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "raw_trace_pointer": str(raw_path.relative_to(output_root)),
        "raw_trace_sha256": result.raw_trace_sha256,
        "output_sha256": _sha256_bytes(result.answer.encode("utf-8")),
        "verifier_passed": verifier_passed,
        "expected_command_observed": parsed["expected_command_observed"],
        "completed_command_count": parsed["completed_command_count"],
        "write_like_command_observed": parsed[
            "write_like_command_observed"
        ],
        "input_snapshot_sha256": _sha256_json(input_snapshot_before),
        "input_snapshot_unchanged": (
            input_snapshot_after == input_snapshot_before
        ),
        "success_markers_passed": answer_markers_passed,
        "harnessx_live_pre_execution_control": result.metadata[
            "harnessx_live_pre_execution_control"
        ],
        "harnessx_pre_allow_count": pre_allow_count,
        "harnessx_post_observation_count": post_count,
        "prompt_provisioned_skill_ids": (
            ["verification-replay-v1"] if arm == "managed" else []
        ),
        "actual_invocation_evidence_complete": result.metadata[
            "actual_invocation_evidence_complete"
        ],
        "actual_invocation_claim": False,
        "raw_secret_scan": {"passed": True, "hits": []},
    }


def run_pilot(
    *,
    output_root: Path,
    executable: Path,
    model_id: str,
    effort: str,
    timeout_s: float,
) -> dict[str, Any]:
    root = output_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    resolved_executable, cli_version = detect_codex_runtime(str(executable))
    task_reports: list[dict[str, Any]] = []
    for task in PILOT_TASKS:
        schedule = _schedule_row(task.task_id)
        contract = _task_contract(task.task_id)
        arms: list[dict[str, Any]] = []
        for arm in schedule["arm_order"]:
            arms.append(
                _run_arm(
                    task=task,
                    arm=arm,
                    output_root=root,
                    executable=Path(resolved_executable),
                    cli_version=cli_version,
                    model_id=model_id,
                    effort=effort,
                    timeout_s=timeout_s,
                )
            )
        by_arm = {arm["arm"]: arm for arm in arms}
        both_passed = all(arm["verifier_passed"] for arm in arms)
        invocation_complete = all(
            arm["actual_invocation_evidence_complete"] for arm in arms
        )
        task_reports.append(
            {
                "task_id": task.task_id,
                "label": task.label,
                "pair_id": schedule["pair_id"],
                "arm_order": schedule["arm_order"],
                "task_contract_sha256": contract["contract_sha256"],
                "exact_command_sha256": _sha256_bytes(
                    task.exact_command.encode("utf-8")
                ),
                "arms": arms,
                "both_arms_verifier_passed": both_passed,
                "observed_execution_turn_delta": (
                    by_arm["baseline"]["execution_turns"]
                    - by_arm["managed"]["execution_turns"]
                ),
                "ledger_append_eligible": both_passed
                and invocation_complete,
                "ledger_blockers": (
                    []
                    if both_passed and invocation_complete
                    else [
                        reason
                        for reason, active in (
                            (
                                "one_or_both_verifiers_failed",
                                not both_passed,
                            ),
                            (
                                "provider_native_skill_invocation_evidence_incomplete",
                                not invocation_complete,
                            ),
                        )
                        if active
                    ]
                ),
            }
        )
    all_arms = [
        arm for task_report in task_reports for arm in task_report["arms"]
    ]
    report: dict[str, Any] = {
        "schema_version": "merlin-personal-workload-account-pilot-v1",
        "pilot_id": "personal-workload-account-auth-2pairs-v1",
        "manifest_sha256": PERSONAL_WORKLOAD_MANIFEST_SHA256,
        "schedule_sha256": PERSONAL_WORKLOAD_SCHEDULE_SHA256,
        "provider_mode": "chatgpt-account-auth",
        "api_key_used": False,
        "github_remote_mutated": False,
        "task_pair_count": len(task_reports),
        "provider_turn_count": len(all_arms),
        "verifier_passed_arm_count": sum(
            arm["verifier_passed"] for arm in all_arms
        ),
        "managed_live_hook_arm_count": sum(
            arm["harnessx_live_pre_execution_control"]
            for arm in all_arms
        ),
        "ledger_appended_observation_count": 0,
        "verified_direct_turn_savings": 0,
        "g_over_s": None,
        "g_over_s_status": (
            "unavailable-pilot-not-eligible-for-longitudinal-ledger"
        ),
        "tasks": task_reports,
        "evidence_boundary": {
            "real_account_auth_codex_turns": True,
            "real_harnessx_live_hooks_on_managed_arms": True,
            "public_safe_bounded_fixtures_only": True,
            "private_workspace_source_exported": False,
            "prompt_skill_exposure_observed": True,
            "provider_native_skill_invocation_observed": False,
            "longitudinal_ledger_promotion_allowed": False,
            "medium_duration_productivity_superiority_claim": False,
        },
    }
    report["report_sha256"] = _sha256_json(report)
    _write_new_json(root / "pilot-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run two real account-auth personal-workload pilot pairs with "
            "live HarnessX hooks on managed arms."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    report = run_pilot(
        output_root=args.output,
        executable=args.codex,
        model_id=args.model,
        effort=args.effort,
        timeout_s=args.timeout,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "task_pair_count": report["task_pair_count"],
                "provider_turn_count": report["provider_turn_count"],
                "verifier_passed_arm_count": report[
                    "verifier_passed_arm_count"
                ],
                "managed_live_hook_arm_count": report[
                    "managed_live_hook_arm_count"
                ],
                "ledger_appended_observation_count": report[
                    "ledger_appended_observation_count"
                ],
                "g_over_s_status": report["g_over_s_status"],
                "report_sha256": report["report_sha256"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
