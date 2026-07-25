"""Score frozen-budget model non-completions as explicit denominator zeroes.

This is a data-layer correction. It never changes the model runner, timeout,
prompt, task bundle, or verifier contract. Raw summary/record files are backed
up before corrected derivatives replace the active pair files.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCORE_SOURCE = "model_noncompletion_timeout_zero"
DEFAULT_REASON = (
    "The model exhausted the frozen agent timeout before verifier execution "
    "and produced no verifiable reward; count as denominator non-pass rather "
    "than excluding or retrying indefinitely."
)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        records.append(record)
    return records


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def record_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("task_id"),
        record.get("condition_id"),
        record.get("arm"),
        record.get("trial_index"),
    )


def eligible_timeout(record: dict[str, Any]) -> bool:
    commands = record.get("commands")
    if not isinstance(commands, dict):
        return False
    agent = commands.get("agent")
    if not isinstance(agent, dict):
        return False
    verifier = commands.get("verifier")
    return bool(
        record.get("status") == "agent_timeout"
        and record.get("passed") is False
        and record.get("reward") is None
        and agent.get("timed_out") is True
        and agent.get("exit_code") == 124
        and verifier is None
    )


def validate_pair_files(
    summary: dict[str, Any],
    jsonl_records: list[dict[str, Any]],
    *,
    task_id: str,
    trial_index: int,
) -> None:
    summary_records = summary.get("records")
    if not isinstance(summary_records, list) or not summary_records:
        raise ValueError("summary must contain non-empty records")
    if len(summary_records) != len(jsonl_records):
        raise ValueError("summary and records.jsonl record counts differ")
    if {record_key(record) for record in summary_records} != {
        record_key(record) for record in jsonl_records
    }:
        raise ValueError("summary and records.jsonl record identities differ")
    if any(record.get("task_id") != task_id for record in summary_records):
        raise ValueError("unexpected task id in summary")
    if any(record.get("trial_index") != trial_index for record in summary_records):
        raise ValueError("unexpected trial index in summary")


def next_backup_path(path: Path, *, label: str) -> Path:
    candidate = path.with_name(f"{path.stem}.raw-agent-timeout-{label}{path.suffix}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.stem}.raw-agent-timeout-{label}-{suffix}{path.suffix}"
        )
        suffix += 1
    return candidate


def apply_timeout_zero_scores(
    run_root: Path,
    *,
    task_id: str,
    trial_index: int,
    label: str | None = None,
    reason: str = DEFAULT_REASON,
    dry_run: bool = False,
) -> dict[str, Any]:
    summary_path = run_root / "summary.json"
    records_path = run_root / "records.jsonl"
    summary = read_json(summary_path)
    jsonl_records = read_jsonl(records_path)
    validate_pair_files(
        summary,
        jsonl_records,
        task_id=task_id,
        trial_index=trial_index,
    )

    summary_records = summary["records"]
    already_scored = {
        record_key(record)
        for record in summary_records
        if record.get("score_source") == SCORE_SOURCE
        and record.get("reward") == 0.0
    }
    targets = [
        record_key(record) for record in summary_records if eligible_timeout(record)
    ]
    if not targets:
        if already_scored:
            return {
                "changed": False,
                "already_scored": len(already_scored),
                "corrected_arms": [],
            }
        raise ValueError("no eligible unscored agent_timeout records")

    jsonl_by_key = {record_key(record): record for record in jsonl_records}
    if any(not eligible_timeout(jsonl_by_key[key]) for key in targets):
        raise ValueError("summary/jsonl timeout eligibility differs")

    label = label or datetime.now(timezone.utc).strftime("%Y%m%d")
    summary_backup = next_backup_path(summary_path, label=label)
    records_backup = next_backup_path(records_path, label=label)
    backup_names = [summary_backup.name, records_backup.name]
    note = (
        f"score_correction_{label}: agent_timeout before verifier is counted "
        f"as denominator non-pass score 0.0; raw files backed up as "
        f"{summary_backup.name} and {records_backup.name}"
    )

    corrections: list[dict[str, Any]] = []
    for record in summary_records:
        if record_key(record) not in targets:
            continue
        record["reward"] = 0.0
        record["score_source"] = SCORE_SOURCE
        record.setdefault("notes", []).append(note)
        corrections.append(
            {
                "task_id": task_id,
                "condition_id": record.get("condition_id"),
                "arm": record.get("arm"),
                "trial_index": trial_index,
                "status": "agent_timeout",
                "score": 0.0,
                "score_source": SCORE_SOURCE,
                "reason": reason,
                "date": label,
                "raw_backups": backup_names,
            }
        )
    for record in jsonl_records:
        if record_key(record) not in targets:
            continue
        record["reward"] = 0.0
        record["score_source"] = SCORE_SOURCE
        record.setdefault("notes", []).append(note)

    summary.setdefault("score_corrections", []).extend(corrections)
    result = {
        "changed": True,
        "already_scored": len(already_scored),
        "corrected_arms": sorted(correction["arm"] for correction in corrections),
        "raw_backups": backup_names,
    }
    if dry_run:
        return result

    shutil.copy2(summary_path, summary_backup)
    shutil.copy2(records_path, records_backup)
    write_json_atomic(summary_path, summary)
    write_jsonl_atomic(records_path, jsonl_records)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--trial-index", type=int, required=True)
    parser.add_argument("--label")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = apply_timeout_zero_scores(
        args.run_root,
        task_id=args.task_id,
        trial_index=args.trial_index,
        label=args.label,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
