"""Build full-denominator execution readiness from immutable oracle evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command_fact(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    command = record.get("commands", {}).get(name)
    if not isinstance(command, dict):
        return None
    return {
        "exit_code": command.get("exit_code"),
        "timed_out": command.get("timed_out") is True,
    }


def classify_execution_evidence(record: dict[str, Any]) -> tuple[int, bool, str]:
    build = command_fact(record, "build")
    verifier = command_fact(record, "verifier")
    oracle = command_fact(record, "oracle")
    reward = record.get("reward")
    if record.get("passed") is True:
        return 50, True, "strict_oracle_and_verifier_pass"
    if build and build.get("exit_code") == 0 and verifier is not None and isinstance(
        reward, (int, float)
    ) and not isinstance(reward, bool):
        return 40, True, "build_and_verifier_scored"
    if build and build.get("exit_code") == 0 and verifier is not None:
        return 35, True, "build_and_verifier_executed"
    if build and build.get("exit_code") == 0 and oracle and oracle.get("exit_code") == 0:
        return 30, False, "build_and_oracle_only"
    if build and build.get("exit_code") == 0:
        return 20, False, "build_only"
    return 0, False, "no_successful_build_evidence"


def build_manifest(base: Path, overlays: list[Path]) -> dict[str, Any]:
    source_paths = [base, *overlays]
    source_data = [json.loads(path.read_text(encoding="utf-8")) for path in source_paths]
    candidates: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for source_index, data in enumerate(source_data):
        for record in data.get("records", []):
            if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
                continue
            rank, _, _ = classify_execution_evidence(record)
            candidates.setdefault(record["task_id"], []).append(
                (rank, source_index, record)
            )

    base_task_ids = [
        record["task_id"]
        for record in source_data[0].get("records", [])
        if isinstance(record, dict) and isinstance(record.get("task_id"), str)
    ]
    if len(base_task_ids) != 87 or len(set(base_task_ids)) != 87:
        raise ValueError("base readiness evidence must contain 87 unique tasks")

    records: list[dict[str, Any]] = []
    for task_id in base_task_ids:
        ranked = candidates.get(task_id, [])
        if not ranked:
            raise ValueError(f"missing readiness evidence for {task_id}")
        rank, source_index, selected = max(ranked, key=lambda item: (item[0], item[1]))
        _, execution_ready, evidence_class = classify_execution_evidence(selected)
        records.append(
            {
                "task_id": task_id,
                "status": selected.get("status"),
                "passed": selected.get("passed") is True,
                "reward": selected.get("reward"),
                "execution_ready": execution_ready,
                "full_denominator_included": True,
                "evidence_class": evidence_class,
                "evidence_rank": rank,
                "source": str(source_paths[source_index]),
                "source_sha256": sha256_file(source_paths[source_index]),
                "source_run_id": source_data[source_index].get("run_id"),
                "commands": {
                    name: fact
                    for name in ("build", "oracle", "verifier")
                    if (fact := command_fact(selected, name)) is not None
                },
            }
        )

    status_counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "full_denominator_with_explicit_execution_evidence",
        "sources": [
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "run_id": data.get("run_id"),
            }
            for path, data in zip(source_paths, source_data, strict=True)
        ],
        "summary": {
            "task_count": len(records),
            "strict_passed": sum(record["passed"] for record in records),
            "execution_ready": sum(record["execution_ready"] for record in records),
            "full_denominator_included": len(records),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_manifest(args.base, args.overlay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
