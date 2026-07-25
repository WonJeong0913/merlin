"""Summarize executable SkillsBench oracle-readiness runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def command_duration(record: dict[str, Any], name: str) -> float:
    command = record.get("commands", {}).get(name, {})
    return float(command.get("duration_sec") or 0.0)


def total_duration(record: dict[str, Any]) -> float:
    return sum(command_duration(record, name) for name in record.get("commands", {}))


def summarize(path: Path, *, slow_limit: int = 10) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records", [])
    status_counts: dict[str, int] = {}
    by_status: dict[str, list[str]] = {}
    for record in records:
        status = str(record.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        by_status.setdefault(status, []).append(str(record.get("task_id")))

    slowest = sorted(
        (
            {
                "task_id": record.get("task_id"),
                "status": record.get("status"),
                "total_duration_sec": round(total_duration(record), 3),
                "build_sec": command_duration(record, "build"),
                "oracle_sec": command_duration(record, "oracle"),
                "verifier_sec": command_duration(record, "verifier"),
            }
            for record in records
        ),
        key=lambda item: item["total_duration_sec"],
        reverse=True,
    )[:slow_limit]

    return {
        "run_id": data.get("run_id"),
        "task_count": len(records),
        "passed": sum(1 for record in records if record.get("passed")),
        "status_counts": dict(sorted(status_counts.items())),
        "by_status": {status: sorted(tasks) for status, tasks in sorted(by_status.items())},
        "slowest": slowest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize oracle-readiness summary.json.")
    parser.add_argument("summary_json")
    parser.add_argument("--slow-limit", type=int, default=10)
    args = parser.parse_args(argv)

    report = summarize(Path(args.summary_json), slow_limit=args.slow_limit)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
