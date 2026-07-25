"""Aggregate repeated paired C0/C1 model-run summaries with provenance checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAPER_CLI_MCP_PROMPT_CONTRACT: dict[str, Any] = {
    "task_user_message": "task_md_body_only",
    "execution_contract_source": "provider_tool_schema",
    "prompt_equals_task_instruction": True,
}
_SAFE_ACCOUNT_AUTH_KEYS = {
    "logged_in",
    "auth_method",
    "api_provider",
    "subscription_type",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_reward(record: dict[str, Any]) -> float | None:
    reward = record.get("reward")
    if (
        isinstance(reward, bool)
        or not isinstance(reward, (int, float))
        or not math.isfinite(float(reward))
        or not 0.0 <= float(reward) <= 1.0
    ):
        return None
    return float(reward)


def _scored_status_is_consistent(record: dict[str, Any], reward: float) -> bool:
    """Require the runner's scored terminal state to agree with reward/pass."""

    if math.isclose(reward, 1.0, rel_tol=0.0, abs_tol=1e-9):
        expected_status = "passed"
        expected_passed = True
    elif math.isclose(reward, 0.0, rel_tol=0.0, abs_tol=1e-9):
        expected_status = "reward_failed"
        expected_passed = False
    else:
        expected_status = "reward_partial"
        expected_passed = False
    return (
        record.get("status") == expected_status
        and record.get("passed") is expected_passed
    )


def _record_is_valid_scored(record: dict[str, Any]) -> bool:
    reward = _valid_reward(record)
    if reward is None:
        return False
    if record.get("invalidated") is True or record.get("valid") is False:
        return False
    if record.get("backend_type") == "B_cli" or record.get("execution_bridge"):
        configuration_audit = record.get("configuration_audit")
        container_exposure = record.get("container_exposure")
        commands = record.get("commands")
        if record.get("credential_forwarded_to_container") is not False:
            return False
        if not isinstance(configuration_audit, dict) or configuration_audit.get("passed") is not True:
            return False
        if not isinstance(container_exposure, dict) or container_exposure.get("passed") is not True:
            return False
        verifier_invocation_count = configuration_audit.get("verifier_invocation_count")
        if (
            not isinstance(verifier_invocation_count, int)
            or isinstance(verifier_invocation_count, bool)
            or verifier_invocation_count != 1
        ):
            return False
        verifier = commands.get("verifier") if isinstance(commands, dict) else None
        if (
            not isinstance(verifier, dict)
            or not isinstance(verifier.get("exit_code"), int)
            or isinstance(verifier.get("exit_code"), bool)
            or verifier.get("exit_code") != 0
            or verifier.get("timed_out") is not False
        ):
            return False
        if not _scored_status_is_consistent(record, reward):
            return False
    return True


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _command_identity(report: Any) -> tuple[Any, ...] | None:
    """Return stable command identity while ignoring duration and diagnostics."""

    if not isinstance(report, dict):
        return None
    argv = report.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return None
    return (
        tuple(argv),
        report.get("exit_code"),
        report.get("timed_out"),
        str(report.get("stdout_tail", "")).strip(),
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _strict_b_cli_violations(
    records: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    *,
    expected_trials: int,
) -> list[str]:
    """Validate the frozen paired B_cli contract before producing an aggregate."""

    violations: list[str] = []

    def add(message: str) -> None:
        if message not in violations:
            violations.append(message)

    def record_label(record: dict[str, Any]) -> str:
        return "/".join(
            str(record.get(key, "missing"))
            for key in ("task_id", "condition_id", "arm", "trial_index")
        )

    for data in summaries:
        run_id = data.get("run_id", "missing-run-id")
        if data.get("invalidated") is True or data.get("valid") is False:
            add(f"{run_id}: summary marked invalid")
        if data.get("prompt_contract") != PAPER_CLI_MCP_PROMPT_CONTRACT:
            add(f"{run_id}: prompt_contract must exactly match the frozen body-only contract")
        backend_contract = data.get("backend_contract")
        if not isinstance(backend_contract, dict) or backend_contract.get("type") != "B_cli":
            add(f"{run_id}: backend_contract.type must be B_cli")
        elif (
            backend_contract.get("auth_mode") != "user_owned_account"
            or backend_contract.get("api_keys_required") is not False
            or backend_contract.get("credentials_forwarded_to_container") is not False
        ):
            add(f"{run_id}: unsafe or mismatched B_cli backend contract")

    for field in (
        "harness_mode",
        "backend_contract",
        "configuration_isolation",
        "image_contract",
        "prompt_contract",
    ):
        values = {_stable_json(data.get(field)) for data in summaries}
        if len(values) != 1:
            add(f"run-level {field} mismatch")

    optional_harness_versions = [
        data.get("harness_version") for data in summaries if data.get("harness_version") is not None
    ]
    if optional_harness_versions and (
        len(optional_harness_versions) != len(summaries)
        or len({_stable_json(value) for value in optional_harness_versions}) != 1
    ):
        add("run-level harness_version mismatch")

    expected_trial_set = set(range(1, expected_trials + 1))
    cells: dict[tuple[str, str, str], set[int]] = {}
    prompt_hashes: dict[tuple[str, str], set[str]] = {}
    task_hashes: dict[str, set[str]] = {}
    c1_skill_hashes: dict[str, set[str]] = {}
    backend_versions: dict[str, set[tuple[Any, ...]]] = {}
    image_ids: dict[str, set[str]] = {}
    backend_cells: set[tuple[Any, ...]] = set()
    harness_cells: set[tuple[Any, ...]] = set()
    resolved_model_cells: set[tuple[str, ...]] = set()
    provider_builtin_skill_sets: set[tuple[str, ...]] = set()
    account_auth_cells: set[str] = set()

    for record in records:
        label = record_label(record)
        if record.get("backend_type") != "B_cli":
            add(f"{label}: backend_type must be B_cli")
        if not _record_is_valid_scored(record):
            add(f"{label}: invalid or unscored B_cli record")

        control_barrier = record.get("control_barrier")
        task_event = (
            control_barrier.get("task_event")
            if isinstance(control_barrier, dict)
            else None
        )
        task_event_count = task_event.get("count") if isinstance(task_event, dict) else None
        warmup_turn_count = (
            control_barrier.get("warmup_model_turn_count")
            if isinstance(control_barrier, dict)
            else None
        )
        if (
            not isinstance(control_barrier, dict)
            or control_barrier.get("passed") is not True
            or not isinstance(task_event_count, int)
            or isinstance(task_event_count, bool)
            or task_event_count != 1
            or not isinstance(warmup_turn_count, int)
            or isinstance(warmup_turn_count, bool)
            or warmup_turn_count != 0
        ):
            add(f"{label}: control barrier/task-event/warmup contract failed")

        requested_model = record.get("model_id")
        tool_trace = record.get("tool_trace")
        configuration_audit = record.get("configuration_audit")
        if not isinstance(configuration_audit, dict):
            configuration_audit = {}
        assistant_models = (
            tool_trace.get("assistant_models")
            if isinstance(tool_trace, dict)
            else None
        )
        if (
            not isinstance(requested_model, str)
            or not requested_model
            or assistant_models != [requested_model]
            or configuration_audit.get("expected_model_id") != requested_model
            or configuration_audit.get("resolved_assistant_models") != [requested_model]
            or configuration_audit.get("resolved_model_matches_request") is not True
        ):
            add(f"{label}: resolved assistant model must exactly match requested model")
        if isinstance(assistant_models, list) and all(
            isinstance(model, str) for model in assistant_models
        ):
            resolved_model_cells.add(tuple(assistant_models))

        provider_builtin_skills = configuration_audit.get("provider_builtin_skills")
        if (
            not isinstance(provider_builtin_skills, list)
            or not all(isinstance(skill, str) and skill for skill in provider_builtin_skills)
            or provider_builtin_skills != sorted(set(provider_builtin_skills))
        ):
            add(f"{label}: provider_builtin_skills must be a canonical exact set")
        else:
            provider_builtin_skill_sets.add(tuple(provider_builtin_skills))

        account_auth = configuration_audit.get("account_auth")
        if (
            not isinstance(account_auth, dict)
            or set(account_auth) != _SAFE_ACCOUNT_AUTH_KEYS
            or account_auth.get("logged_in") is not True
            or account_auth.get("auth_method") != "claude.ai"
            or account_auth.get("api_provider") != "firstParty"
            or not isinstance(account_auth.get("subscription_type"), str)
            or not account_auth.get("subscription_type")
        ):
            add(f"{label}: unsafe or incomplete account_auth evidence")
        else:
            account_auth_cells.add(_stable_json(account_auth))

        commands = record.get("commands")
        verifier = commands.get("verifier") if isinstance(commands, dict) else None
        if (
            not isinstance(verifier, dict)
            or not isinstance(verifier.get("exit_code"), int)
            or isinstance(verifier.get("exit_code"), bool)
            or verifier.get("exit_code") != 0
            or verifier.get("timed_out") is not False
        ):
            add(f"{label}: verifier must exit 0 without timeout")
        reward = _valid_reward(record)
        if reward is None or not _scored_status_is_consistent(record, reward):
            add(f"{label}: reward/passed/status contract is inconsistent")

        task_id = str(record.get("task_id"))
        condition_id = str(record.get("condition_id"))
        arm = str(record.get("arm"))
        trial_index = record.get("trial_index")
        if arm not in {"C0", "C1"}:
            add(f"{label}: arm must be C0 or C1")
        if not isinstance(trial_index, int) or isinstance(trial_index, bool):
            add(f"{label}: trial_index must be an integer")
        else:
            cells.setdefault((task_id, condition_id, arm), set()).add(trial_index)

        backend_cells.add(
            (
                record.get("backend_type"),
                record.get("backend"),
                record.get("model_id"),
                record.get("effort"),
                record.get("runtime_effort"),
                record.get("auth_mode"),
            )
        )
        harness_cells.add(
            (
                record.get("harness_mode"),
                record.get("execution_bridge"),
                _stable_json(record.get("harness_version")),
            )
        )

        prompt_hash = record.get("prompt_sha256")
        task_hash = record.get("task_instruction_sha256")
        if not _is_sha256(prompt_hash):
            add(f"{label}: missing or malformed prompt_sha256")
        else:
            prompt_hashes.setdefault((task_id, condition_id), set()).add(prompt_hash)
        if not _is_sha256(task_hash):
            add(f"{label}: missing or malformed task_instruction_sha256")
        else:
            task_hashes.setdefault(task_id, set()).add(task_hash)
        if _is_sha256(prompt_hash) and _is_sha256(task_hash) and prompt_hash != task_hash:
            add(f"{label}: body-only prompt_sha256 must equal task_instruction_sha256")

        skill = record.get("skill_delivery")
        if not isinstance(skill, dict):
            add(f"{label}: missing skill_delivery manifest")
        elif arm == "C0":
            if (
                skill.get("mode") != "none"
                or skill.get("file_count") != 0
                or skill.get("total_bytes") != 0
                or skill.get("sha256") is not None
            ):
                add(f"{label}: C0 must have an empty skill manifest")
        elif arm == "C1":
            skill_hash = skill.get("sha256")
            if (
                skill.get("mode") == "none"
                or not isinstance(skill.get("file_count"), int)
                or skill.get("file_count", 0) < 1
                or not _is_sha256(skill_hash)
            ):
                add(f"{label}: C1 must have a non-empty hashed skill manifest")
            else:
                c1_skill_hashes.setdefault(task_id, set()).add(skill_hash)

        version_identity = _command_identity(
            commands.get("backend_version") if isinstance(commands, dict) else None
        )
        if (
            version_identity is None
            or version_identity[1] != 0
            or version_identity[2] is not False
            or not version_identity[3]
        ):
            add(f"{label}: missing or failed backend version probe")
        else:
            backend_versions.setdefault(condition_id, set()).add(version_identity)

        image_report = commands.get("image_id") if isinstance(commands, dict) else None
        image_identity = _command_identity(image_report)
        if (
            image_identity is None
            or image_identity[1] != 0
            or image_identity[2] is not False
            or not image_identity[3]
        ):
            add(f"{label}: missing or failed image identity probe")
        else:
            image_ids.setdefault(task_id, set()).add(image_identity[3])

    if len(backend_cells) != 1:
        add("backend/model/effort/auth mismatch across records")
    if len(harness_cells) != 1:
        add("harness mode/version/execution bridge mismatch across records")
    if len(resolved_model_cells) != 1:
        add("resolved assistant model mismatch across records")
    if len(provider_builtin_skill_sets) != 1:
        add("provider_builtin_skills exact set mismatch across arms/trials")
    if len(account_auth_cells) != 1:
        add("account_auth mismatch across arms/trials")

    task_conditions = {(record["task_id"], record["condition_id"]) for record in records}
    for task_id, condition_id in sorted(task_conditions):
        for arm in ("C0", "C1"):
            observed = cells.get((task_id, condition_id, arm), set())
            if observed != expected_trial_set:
                add(
                    f"{task_id}/{condition_id}/{arm}: trial set {sorted(observed)} "
                    f"does not match {sorted(expected_trial_set)}"
                )

    for (task_id, condition_id), values in sorted(prompt_hashes.items()):
        if len(values) != 1:
            add(f"{task_id}/{condition_id}: prompt_sha256 mismatch")
    for task_id, values in sorted(task_hashes.items()):
        if len(values) != 1:
            add(f"{task_id}: task_instruction_sha256 mismatch")
    for task_id, values in sorted(c1_skill_hashes.items()):
        if len(values) != 1:
            add(f"{task_id}: C1 skill manifest sha256 mismatch")
    for condition_id, values in sorted(backend_versions.items()):
        if len(values) != 1:
            add(f"{condition_id}: backend version mismatch")
    for task_id, values in sorted(image_ids.items()):
        if len(values) != 1:
            add(f"{task_id}: image identity mismatch")

    for data in summaries:
        run_id = data.get("run_id", "missing-run-id")
        top_harness = data.get("harness_mode")
        for record in data.get("records", []):
            if record.get("harness_mode") != top_harness:
                add(f"{run_id}: record harness_mode differs from summary")
        top_versions = data.get("backend_versions")
        if not isinstance(top_versions, dict):
            add(f"{run_id}: missing backend_versions")
            continue
        for condition_id in {str(record.get("condition_id")) for record in data.get("records", [])}:
            top_identity = _command_identity(top_versions.get(condition_id))
            record_identities = {
                _command_identity(
                    record.get("commands", {}).get("backend_version")
                    if isinstance(record.get("commands"), dict)
                    else None
                )
                for record in data.get("records", [])
                if str(record.get("condition_id")) == condition_id
            }
            if top_identity is None or record_identities != {top_identity}:
                add(f"{run_id}/{condition_id}: summary and record backend version mismatch")

    return violations


def _arm_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewards = [float(record["reward"]) for record in records if record.get("reward") is not None]
    valid_records = [record for record in records if _record_is_valid_scored(record)]
    valid_rewards = [float(record["reward"]) for record in valid_records]
    wall_times = [float(record["wall_time_sec"]) for record in records if record.get("wall_time_sec") is not None]
    return {
        "n": len(records),
        "passed": sum(bool(record.get("passed")) for record in records),
        "pass_rate": sum(bool(record.get("passed")) for record in records) / len(records) if records else None,
        "reward_observed": len(rewards),
        "mean_observed_reward": sum(rewards) / len(rewards) if rewards else None,
        "valid_scored": len(valid_records),
        "mean_valid_reward": sum(valid_rewards) / len(valid_rewards) if valid_rewards else None,
        "invalid_or_unscored": len(records) - len(valid_records),
        "status_counts": dict(sorted(Counter(record.get("status", "unknown") for record in records).items())),
        "mean_wall_time_sec": sum(wall_times) / len(wall_times) if wall_times else None,
    }


def _aggregate_benchmark_eligibility(
    summaries: list[dict[str, Any]],
    *,
    data_contract_complete: bool,
) -> dict[str, Any]:
    """Conservatively combine source-declared paper eligibility.

    Data completeness is necessary but never sufficient for a paper-aligned
    claim.  A source must explicitly declare its paper criterion true; missing
    or contradictory declarations remain ineligible.
    """

    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason and reason not in reasons:
            reasons.append(reason)

    if not data_contract_complete:
        add("aggregate data contract is incomplete")

    all_sources_paper_eligible = bool(summaries)
    for data in summaries:
        run_id = str(data.get("run_id", "missing-run-id"))
        source = data.get("benchmark_eligibility")
        if not isinstance(source, dict):
            all_sources_paper_eligible = False
            add(f"{run_id}: missing benchmark_eligibility declaration")
            continue

        declared_flags = [
            source[key]
            for key in ("paper_eligible", "skillsbench_paper_c0_c1")
            if key in source
        ]
        source_eligible = bool(declared_flags) and all(flag is True for flag in declared_flags)
        if source_eligible:
            continue

        all_sources_paper_eligible = False
        source_reasons = source.get("reasons")
        if isinstance(source_reasons, list):
            for reason in source_reasons:
                if isinstance(reason, str) and reason.strip():
                    add(reason.strip())
        if not isinstance(source_reasons, list) or not any(
            isinstance(reason, str) and reason.strip() for reason in source_reasons
        ):
            add(f"{run_id}: source does not declare paper eligibility")

    paper_eligible = data_contract_complete and all_sources_paper_eligible
    return {
        "data_contract_complete": data_contract_complete,
        "paper_eligible": paper_eligible,
        "reasons": [] if paper_eligible else reasons,
    }


def aggregate_model_trials(summary_paths: list[Path], *, expected_trials: int = 3) -> dict[str, Any]:
    if expected_trials < 1:
        raise ValueError("expected_trials must be >= 1")
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    run_contracts: list[dict[str, Any]] = []
    summary_documents: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, int]] = set()

    for path in summary_paths:
        for marker_name in ("INVALIDATED.json", "NONFINAL.json"):
            if (path.parent / marker_name).exists():
                raise ValueError(
                    f"refusing {marker_name.removesuffix('.json').lower()} run summary: {path}"
                )
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError(f"invalid run summary structure: {path}")
        summary_documents.append(data)
        sources.append({"path": str(path), "sha256": _sha256(path), "run_id": data.get("run_id")})
        run_contracts.append(
            {
                "run_id": data.get("run_id"),
                "harness_mode": data.get("harness_mode"),
                "backend_contract": data.get("backend_contract"),
                "configuration_isolation": data.get("configuration_isolation"),
                "prompt_contract": data.get("prompt_contract"),
                "benchmark_eligibility": data.get("benchmark_eligibility"),
            }
        )
        for record in data.get("records", []):
            trial_index = int(record.get("trial_index", data.get("trial_control", {}).get("trial_index", 0)))
            key = (record["task_id"], record["condition_id"], record["arm"], trial_index)
            if key in seen_keys:
                raise ValueError(f"duplicate task/condition/arm/trial record: {key}")
            seen_keys.add(key)
            item = dict(record)
            item["trial_index"] = trial_index
            item["source_summary"] = str(path)
            records.append(item)

    b_cli_requested = any(
        data.get("backend_contract", {}).get("type") == "B_cli"
        for data in summary_documents
        if isinstance(data.get("backend_contract"), dict)
    ) or any(record.get("backend_type") == "B_cli" for record in records)
    if b_cli_requested:
        violations = _strict_b_cli_violations(
            records,
            summary_documents,
            expected_trials=expected_trials,
        )
        if violations:
            raise ValueError("refusing invalid B_cli aggregate: " + "; ".join(violations))

    harness_modes = {record.get("harness_mode") for record in records}
    models = {(record.get("backend"), record.get("model_id"), record.get("effort")) for record in records}
    if len(harness_modes) != 1:
        raise ValueError(f"mixed harness modes: {sorted(str(item) for item in harness_modes)}")
    if len(models) != 1:
        raise ValueError(f"mixed backend/model/effort cells: {sorted(str(item) for item in models)}")

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        group_key = f"{record['task_id']}:{record['condition_id']}:{record['arm']}"
        groups.setdefault(group_key, []).append(record)
    by_task_condition_arm = {
        key: _arm_summary(sorted(items, key=lambda item: item["trial_index"]))
        for key, items in sorted(groups.items())
    }

    paired_rows: list[dict[str, Any]] = []
    record_by_pair = {
        (record["task_id"], record["condition_id"], record["trial_index"], record["arm"]): record
        for record in records
    }
    pair_ids = sorted({key[:3] for key in record_by_pair})
    for task_id, condition_id, trial_index in pair_ids:
        c0 = record_by_pair.get((task_id, condition_id, trial_index, "C0"))
        c1 = record_by_pair.get((task_id, condition_id, trial_index, "C1"))
        c0_reward = c0.get("reward") if c0 else None
        c1_reward = c1.get("reward") if c1 else None
        c0_valid = bool(c0 and _record_is_valid_scored(c0))
        c1_valid = bool(c1 and _record_is_valid_scored(c1))
        paired_rows.append(
            {
                "task_id": task_id,
                "condition_id": condition_id,
                "trial_index": trial_index,
                "c0_status": c0.get("status") if c0 else "missing",
                "c1_status": c1.get("status") if c1 else "missing",
                "c0_reward": c0_reward,
                "c1_reward": c1_reward,
                "c0_valid_scored": c0_valid,
                "c1_valid_scored": c1_valid,
                "valid_pair": c0_valid and c1_valid,
                "reward_delta_c1_minus_c0": (
                    float(c1_reward) - float(c0_reward)
                    if c0_valid and c1_valid
                    else None
                ),
            }
        )
    observed_deltas = [
        row["reward_delta_c1_minus_c0"]
        for row in paired_rows
        if row["reward_delta_c1_minus_c0"] is not None
    ]
    complete_groups = bool(by_task_condition_arm) and all(
        summary["n"] == expected_trials and summary["valid_scored"] == expected_trials
        for summary in by_task_condition_arm.values()
    )
    complete_pairs = bool(paired_rows) and all(row["valid_pair"] for row in paired_rows)
    data_contract_complete = complete_groups and complete_pairs
    benchmark_eligibility = _aggregate_benchmark_eligibility(
        summary_documents,
        data_contract_complete=data_contract_complete,
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_trials_per_arm": expected_trials,
        # Backward-compatible alias.  This is data completeness, not a claim
        # that the aggregate reproduces the paper's evaluation contract.
        "complete": data_contract_complete,
        "data_contract_complete": data_contract_complete,
        "paper_eligible": benchmark_eligibility["paper_eligible"],
        "benchmark_eligibility": benchmark_eligibility,
        "harness_mode": next(iter(harness_modes)) if harness_modes else None,
        "backend_model_effort": list(next(iter(models))) if models else None,
        "source_summaries": sources,
        "run_contracts": run_contracts,
        "by_task_condition_arm": by_task_condition_arm,
        "paired_trials": paired_rows,
        "paired_reward_delta_observed": len(observed_deltas),
        "paired_mean_reward_delta_c1_minus_c0": (
            sum(observed_deltas) / len(observed_deltas) if observed_deltas else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, nargs="+")
    parser.add_argument("--expected-trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    result = aggregate_model_trials(args.summary, expected_trials=args.expected_trials)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "complete": result["complete"],
        "data_contract_complete": result["data_contract_complete"],
        "paper_eligible": result["paper_eligible"],
        "benchmark_ineligibility_reasons": result["benchmark_eligibility"]["reasons"],
        "paired_reward_delta_observed": result["paired_reward_delta_observed"],
        "paired_mean_reward_delta_c1_minus_c0": result["paired_mean_reward_delta_c1_minus_c0"],
    }, sort_keys=True))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
